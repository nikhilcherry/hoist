"""Seamless rollover for Docker apps hoist builds itself (Dockerfile, buildpack).

The app's systemd unit runs `hoist front <name>` instead of `docker compose up`.
That process owns the app's public port and forwards every connection to the
running container, so the port never stops listening. A redeploy then works
like this:

  1. hoist builds the new image and starts a second container beside the old
     one (`compose up --scale app=2 --no-recreate`);
  2. it asks this supervisor to `swap` over a unix socket;
  3. the supervisor waits until the new container answers HTTP, points new
     connections at it, and removes the old container after a short drain.

A new version that never answers is removed and the old one keeps serving. Only
single-service, volume-less stacks that hoist generated itself use this: running
two copies at once is safe for them and could corrupt anything that owns data.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from . import config, containers

SERVICE = "app"                 # the one service of a generated compose file
DRAIN_SECONDS = 8.0             # how long the old container lives on after a swap
HEALTH_TIMEOUT = 90.0           # how long the new container gets to answer
SUPERVISE_EVERY = 10.0    # a crash is noticed within this long; checks cost no process spawn
RESTART_BACKOFF = 5.0


def socket_path(name: str) -> Path:
    return config.STATE_DIR / "run" / f"{name}.sock"


def front_command(name: str) -> str:
    """What the app's systemd unit runs."""
    return f"{sys.executable} -m hoist front {name}"


# --- Docker, in one place so tests can swap it out ---------------------------------


class _UnixHTTPConnection(http.client.HTTPConnection):
    """http.client over a unix socket (the Docker Engine API)."""

    def __init__(self, path: str, timeout: float = 5.0):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        import socket as _socket
        self.sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _engine_socket() -> Optional[str]:
    """The Docker Engine's unix socket, when it is the plain local one."""
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        return host[len("unix://"):]
    return "/var/run/docker.sock" if not host else None


class Docker:
    def __init__(self, project: str, workdir: Callable[[], Path], compose_file: str = containers.GENERATED_COMPOSE,
                 env: Optional[dict] = None):
        self.socket = _engine_socket()
        self.project = project
        self.workdir = workdir          # called every time: `current` moves on each deploy
        self.compose_file = compose_file
        self.env = env or {}

    def _docker(self) -> list[str]:
        return shlex.split(containers.docker())

    def _run(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([*self._docker(), *args], capture_output=True, text=True, timeout=timeout)

    def compose_argv(self, *args: str) -> list[str]:
        return [*self._docker(), "compose", "-p", self.project, "-f", self.compose_file, *args]

    def _compose(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        return subprocess.run(self.compose_argv(*args), cwd=self.workdir(), capture_output=True, text=True,
                              timeout=timeout, env=dict(os.environ, **self.env))

    def running(self) -> list[dict]:
        """The project's running app containers: id, created, ip (newest last).

        Asks the Docker Engine over its unix socket: no process is spawned, which matters
        because this runs on a timer for every app. Falls back to the CLI if the socket
        is not usable (a remote or rootless daemon, for instance).
        """
        rows = self._running_via_socket()
        return rows if rows is not None else self._running_via_cli()

    def _running_via_socket(self) -> Optional[list[dict]]:
        sock_path = self.socket
        if not sock_path or not os.path.exists(sock_path):
            return None
        import urllib.parse as _up
        filters = json.dumps({"label": [f"com.docker.compose.project={self.project}",
                                        f"com.docker.compose.service={SERVICE}"], "status": ["running"]})
        conn = _UnixHTTPConnection(sock_path)
        try:
            conn.request("GET", "/containers/json?filters=" + _up.quote(filters))
            resp = conn.getresponse()
            if resp.status != 200:
                return None
            items = json.loads(resp.read())
        except (OSError, http.client.HTTPException, ValueError):
            return None
        finally:
            conn.close()
        rows = []
        for item in items:
            nets = (item.get("NetworkSettings") or {}).get("Networks") or {}
            ip = next((n.get("IPAddress") for n in nets.values() if n.get("IPAddress")), "")
            rows.append({"id": item["Id"], "created": f"{int(item.get('Created', 0)):012d}", "ip": ip})
        return sorted(rows, key=lambda r: (r["created"], r["id"]))

    def _running_via_cli(self) -> list[dict]:
        ids = self._run("ps", "-q", "--filter", f"label=com.docker.compose.project={self.project}",
                        "--filter", f"label=com.docker.compose.service={SERVICE}").stdout.split()
        if not ids:
            return []
        fmt = "{{.Id}}|{{.Created}}|{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
        out = self._run("inspect", "-f", fmt, *ids).stdout
        rows = []
        for line in out.splitlines():
            cid, _, rest = line.partition("|")
            created, _, ips = rest.partition("|")
            ip = next(iter(ips.split()), "")
            if cid:
                rows.append({"id": cid, "created": created, "ip": ip})
        return sorted(rows, key=lambda r: r["created"])

    def up(self) -> bool:
        return self._compose("up", "-d", "--remove-orphans").returncode == 0

    def scale_up(self, release: Path) -> tuple[bool, str]:
        """Start one more container, from the new release, beside the one that is serving."""
        done = subprocess.run(self.compose_argv("up", "-d", "--no-recreate", "--scale", f"{SERVICE}=2"),
                              cwd=release, capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, **self.env))
        return done.returncode == 0, (done.stderr or done.stdout).strip()[-200:]

    def remove(self, cid: str) -> None:
        self._run("stop", "-t", "3", cid, timeout=30)
        self._run("rm", "-f", cid, timeout=30)

    def stop_all(self) -> None:
        self._compose("stop", "-t", "5", timeout=60)

    def logs_argv(self) -> list[str]:
        return self.compose_argv("logs", "-f", "--no-color", "--tail", "20")


def http_answers(ip: str, port: int, timeout: float = 1.5) -> bool:
    """True once anything on ip:port answers HTTP (any status counts as alive)."""
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        conn.request("GET", "/", headers={"User-Agent": "hoist-front"})
        conn.getresponse().read(1)
        return True
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


# --- the supervisor ----------------------------------------------------------------


class Front:
    def __init__(self, name: str, docker, bind: str, port: int, inner: int, *,
                 drain: float = DRAIN_SECONDS, health_timeout: float = HEALTH_TIMEOUT,
                 probe: Callable[[str, int], bool] = http_answers, control: Optional[Path] = None):
        self.name, self.docker, self.bind, self.port, self.inner = name, docker, bind, port, inner
        self.drain, self.health_timeout, self.probe = drain, health_timeout, probe
        self.control = control if control is not None else socket_path(name)
        self.target: Optional[tuple[str, int]] = None
        self.target_id: Optional[str] = None
        self.stopping: Optional[asyncio.Event] = None
        self._lock: Optional[asyncio.Lock] = None
        self._wake: Optional[asyncio.Event] = None
        self._servers: list = []
        self._last_up = 0.0
        self.swaps = 0

    # -- finding the container ---------------------------------------------------------

    async def refresh_target(self) -> None:
        """Point at the container we were using if it still runs, else the newest one."""
        rows = [r for r in await asyncio.to_thread(self.docker.running) if r["ip"]]
        mine = next((r for r in rows if r["id"] == self.target_id), None)
        if mine is not None:
            self.target = (mine["ip"], self.inner)   # also repairs a target cleared elsewhere
        elif rows:
            newest = rows[-1]
            self.target, self.target_id = (newest["ip"], self.inner), newest["id"]
        else:
            self.target, self.target_id = None, None

    # -- forwarding --------------------------------------------------------------------

    CONNECT_PATIENCE = 3.0  # a request that arrives while the container is still starting waits, not fails

    async def _connect(self):
        deadline = time.monotonic() + self.CONNECT_PATIENCE
        while True:
            if self.target is None:
                await self.refresh_target()
            if self.target is not None:
                try:
                    return await asyncio.wait_for(asyncio.open_connection(*self.target), 1.0)
                except (OSError, asyncio.TimeoutError):
                    pass
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.1)
            await self.refresh_target()   # the container may have been replaced or restarted
            if self.target is None and self._wake is not None:
                self._wake.set()          # nothing is running and someone is asking: do not wait for the timer

    async def _pipe(self, reader, writer) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (OSError, asyncio.CancelledError, ConnectionError):
            pass
        finally:
            try:
                if writer.can_write_eof():
                    writer.write_eof()
                else:
                    writer.close()
            except (OSError, RuntimeError):
                pass

    async def _client(self, reader, writer) -> None:
        upstream = await self._connect()
        if upstream is None:
            writer.close()
            return
        up_reader, up_writer = upstream
        try:
            await asyncio.gather(self._pipe(reader, up_writer), self._pipe(up_reader, writer))
        finally:
            for w in (writer, up_writer):
                try:
                    w.close()
                except OSError:
                    pass

    # -- swapping ----------------------------------------------------------------------

    async def _discard_stale(self) -> None:
        """Remove every container that is not serving, now.

        A deploy that arrives while the previous old container is still draining
        supersedes it. Without this, `--scale 2` finds two containers already and
        starts nothing, and the "new" one would be the stale old version.
        """
        for row in await asyncio.to_thread(self.docker.running):
            if row["id"] != self.target_id:
                await asyncio.to_thread(self.docker.remove, row["id"])

    async def swap(self, release: Optional[Path] = None) -> str:
        """Start the new version (when given a release), then point new connections at it
        once it answers. Returns "ok" or "err: why"; on any error the old one keeps serving."""
        async with self._lock:  # type: ignore[union-attr]
            if self.target_id is None:
                await self.refresh_target()
            if release is not None:           # deploying: this supersedes anything still draining
                await self._discard_stale()
                started, why = await asyncio.to_thread(self.docker.scale_up, release)
                if not started:
                    return f"err: could not start the new version: {why}"
            rows = [r for r in await asyncio.to_thread(self.docker.running) if r["ip"]]
            fresh = [r for r in rows if r["id"] != self.target_id]
            if not fresh:
                return "err: no new container is running"
            new = fresh[-1]
            deadline = time.monotonic() + self.health_timeout
            while True:
                if await asyncio.to_thread(self.probe, new["ip"], self.inner):
                    break
                alive = {r["id"] for r in await asyncio.to_thread(self.docker.running)}
                if new["id"] not in alive:
                    await asyncio.to_thread(self.docker.remove, new["id"])  # a dead one would be reused
                    return "err: the new container exited before it answered"
                if time.monotonic() > deadline:
                    await asyncio.to_thread(self.docker.remove, new["id"])
                    return f"err: the new version did not answer within {int(self.health_timeout)} s"
                await asyncio.sleep(0.2)
            old = [r["id"] for r in rows if r["id"] != new["id"]]
            self.target, self.target_id = (new["ip"], self.inner), new["id"]
            self.swaps += 1
            for cid in old:
                asyncio.get_running_loop().call_later(
                    self.drain, lambda c=cid: asyncio.ensure_future(asyncio.to_thread(self.docker.remove, c)))
            return "ok"

    async def _control(self, reader, writer) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 5)).decode().strip()
            verb, _, arg = line.partition(" ")
            if verb == "swap":
                reply = await self.swap(Path(arg) if arg else None)
            elif line == "status":
                reply = json.dumps({"target": self.target, "swaps": self.swaps})
            else:
                reply = "err: unknown command"
            writer.write((reply + "\n").encode())
            await writer.drain()
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()

    # -- keeping it alive --------------------------------------------------------------

    async def _supervise(self) -> None:
        assert self.stopping is not None
        while not self.stopping.is_set():
            if not self._lock.locked():  # type: ignore[union-attr]
                rows = await asyncio.to_thread(self.docker.running)
                if not rows:
                    self.target, self.target_id = None, None
                    if time.monotonic() - self._last_up >= RESTART_BACKOFF:
                        self._last_up = time.monotonic()
                        print(f"{self.name}: no container running, starting one", flush=True)
                        await asyncio.to_thread(self.docker.up)
                elif self.target_id not in {r["id"] for r in rows}:
                    await self.refresh_target()
            stop_wait = asyncio.ensure_future(self.stopping.wait())
            wake_wait = asyncio.ensure_future(self._wake.wait())          # type: ignore[union-attr]
            await asyncio.wait({stop_wait, wake_wait}, timeout=SUPERVISE_EVERY, return_when=asyncio.FIRST_COMPLETED)
            for fut in (stop_wait, wake_wait):
                fut.cancel()
            self._wake.clear()                                            # type: ignore[union-attr]

    async def _logs(self) -> None:
        """Stream the container's output into the journal, restarting the stream if it ends."""
        assert self.stopping is not None
        while not self.stopping.is_set():
            proc = await asyncio.create_subprocess_exec(*self.docker.logs_argv())
            waiter = asyncio.ensure_future(proc.wait())
            stopper = asyncio.ensure_future(self.stopping.wait())
            await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if proc.returncode is None:
                proc.terminate()
            for fut in (waiter, stopper):
                fut.cancel()
            if not self.stopping.is_set():
                await asyncio.sleep(2)

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        self.stopping = asyncio.Event()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._servers.append(await asyncio.start_server(self._client, self.bind, self.port, reuse_address=True))
        self.control.parent.mkdir(parents=True, exist_ok=True)
        self.control.unlink(missing_ok=True)
        ctl = await asyncio.start_unix_server(self._control, path=str(self.control))
        os.chmod(self.control, 0o600)
        self._servers.append(ctl)
        self._last_up = time.monotonic()   # the caller brings the container up first; do not race it
        self._tasks = [asyncio.ensure_future(self._supervise())]

    async def run_logs(self) -> None:
        self._tasks.append(asyncio.ensure_future(self._logs()))

    async def close(self) -> None:
        assert self.stopping is not None
        self.stopping.set()
        for server in self._servers:
            server.close()
        for task in getattr(self, "_tasks", []):
            task.cancel()
        self.control.unlink(missing_ok=True)


async def serve(name: str, docker, bind: str, port: int, inner: int) -> int:
    front = Front(name, docker, bind, port, inner)
    await front.start()
    print(f"{name}: forwarding {bind}:{port} to its container (port {inner})", flush=True)
    await asyncio.to_thread(docker.up)       # bring the container up (cheap if it is already there)
    front._last_up = time.monotonic()
    await front.refresh_target()
    await front.run_logs()
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, done.set)
    await done.wait()
    await front.close()
    await asyncio.to_thread(docker.stop_all)
    return 0


# --- what `hoist sync` calls ---------------------------------------------------------


def request(name: str, command: str = "swap", timeout: float = HEALTH_TIMEOUT + 30) -> Optional[str]:
    """Ask the running supervisor to do something. None when it is not running."""
    import socket as _socket
    path = socket_path(name)
    if not path.exists():
        return None
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall((command + "\n").encode())
        return sock.makefile().readline().strip()
    except OSError:
        return None
    finally:
        sock.close()


def rollover(name: str, release: Path, env: Optional[dict] = None) -> tuple[Optional[bool], str]:
    """Have the supervisor start the new version beside the old one and switch to it.

    (`env` is unused: the supervisor already carries the app's environment from its unit.)

    Returns (True, "") when traffic moved, (False, why) when the new version
    failed (the old one is untouched), and (None, "") when no supervisor is
    running so the caller must fall back to a plain restart.
    """
    if not socket_path(name).exists():
        return None, ""
    reply = request(name, f"swap {release.resolve()}")
    if reply is None:
        return None, ""
    if reply == "ok":
        return True, ""
    return False, reply[5:] if reply.startswith("err: ") else reply
