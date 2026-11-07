"""`hoist front`: the supervisor that makes container redeploys seamless.

The containers here are real TCP servers on loopback aliases (127.0.0.2,
127.0.0.3...), and Docker is a fake that lists, removes and "starts" them, so
these tests exercise the forwarding, the swap and the recovery for real,
without needing Docker.
"""

from __future__ import annotations

import asyncio
import http.client
import itertools
import socket

import pytest

from hoist import front


@pytest.fixture(autouse=True)
def quiet_supervisor(monkeypatch):
    """The supervisor starts a container whenever none is running, which is its job but
    would race the tests that launch their own. Only the recovery test turns it back on."""
    monkeypatch.setattr(front, "RESTART_BACKOFF", 1e9)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.2", 0))
        return s.getsockname()[1]


class FakeDocker:
    """Stands in for hoist.front.Docker; each 'container' is a tiny HTTP server."""

    def __init__(self, inner: int):
        self.inner = inner
        self.boxes: dict[str, dict] = {}
        self.removed: list[str] = []
        self.scaled: list = []
        self.ups = 0
        self._ips = itertools.count(2)
        self._clock = itertools.count(1)

    async def launch(self, label: str, *, healthy: bool = True) -> str:
        ip = f"127.0.0.{next(self._ips)}"
        cid = f"{label}-{ip}"

        async def handle(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                body = f"VERSION: {label}".encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(handle, ip, self.inner) if healthy else None
        self.boxes[cid] = {"id": cid, "created": f"{next(self._clock):09d}", "ip": ip, "server": server}
        return cid

    # -- the interface Front uses --
    def running(self):
        return [{"id": b["id"], "created": b["created"], "ip": b["ip"]} for b in self.boxes.values()]

    def up(self):
        self.ups += 1
        if not self.boxes:
            asyncio.run_coroutine_threadsafe(self.launch(f"restarted{self.ups}"), self.loop).result(5)
        return True

    def remove(self, cid):
        box = self.boxes.pop(cid, None)
        self.removed.append(cid)
        if box and box["server"]:
            self.loop.call_soon_threadsafe(box["server"].close)

    def scale_up(self, release):
        """Start the 'new version' the way compose would: one more container beside the others."""
        self.scaled.append(release)
        label = f"new{len(self.scaled)}"
        asyncio.run_coroutine_threadsafe(self.launch(label), self.loop).result(5)
        return True, ""

    def stop_all(self):
        pass

    def logs_argv(self):
        return ["true"]


def get(port: int) -> str:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        return resp.read().decode()
    finally:
        conn.close()


async def make(tmp_path, **kw):
    inner = _free_port()
    docker = FakeDocker(inner)
    docker.loop = asyncio.get_running_loop()
    f = front.Front("t", docker, "127.0.0.1", 0, inner, control=tmp_path / "t.sock", **kw)
    await f.start()
    return f, docker, f._servers[0].sockets[0].getsockname()[1]


def test_traffic_is_forwarded_to_the_running_container(tmp_path):
    async def go():
        f, docker, port = await make(tmp_path)
        await docker.launch("v1")
        assert await asyncio.to_thread(get, port) == "VERSION: v1"
        await f.close()
    asyncio.run(go())


def test_a_swap_loses_no_requests_and_removes_the_old_container(tmp_path):
    """The point of the feature: hammer the public port while the version changes."""
    async def go():
        f, docker, port = await make(tmp_path, drain=0.3)
        await docker.launch("v1")
        await f.refresh_target()
        seen, failures, stop = [], [], asyncio.Event()

        async def hammer():
            while not stop.is_set():
                try:
                    seen.append(await asyncio.to_thread(get, port))
                except Exception as exc:  # noqa: BLE001
                    failures.append(repr(exc))
                await asyncio.sleep(0.005)

        task = asyncio.ensure_future(hammer())
        await asyncio.sleep(0.3)
        await docker.launch("v2")
        assert await f.swap() == "ok"
        await asyncio.sleep(0.6)
        stop.set()
        await task
        await f.close()
        assert failures == [], failures[:3]
        assert "VERSION: v1" in seen and seen[-1] == "VERSION: v2"
        # once it switched it never went back, and the old one was cleaned up after the drain
        first_v2 = seen.index("VERSION: v2")
        assert set(seen[first_v2:]) == {"VERSION: v2"}
        assert docker.removed == ["v1-127.0.0.2"]
    asyncio.run(go())


def test_a_new_version_that_never_answers_is_discarded_and_the_old_keeps_serving(tmp_path):
    async def go():
        f, docker, port = await make(tmp_path, health_timeout=0.8)
        await docker.launch("v1")
        await f.refresh_target()
        await docker.launch("broken", healthy=False)       # running, but nothing listens
        reply = await f.swap()
        assert reply.startswith("err:") and "did not answer" in reply
        assert await asyncio.to_thread(get, port) == "VERSION: v1"
        assert any(r.startswith("broken") for r in docker.removed) and "v1-127.0.0.2" not in docker.removed
        await f.close()
    asyncio.run(go())


def test_a_swap_with_no_new_container_is_an_error_not_a_crash(tmp_path):
    async def go():
        f, docker, _ = await make(tmp_path)
        await docker.launch("v1")
        await f.refresh_target()
        assert (await f.swap()).startswith("err:")
        await f.close()
    asyncio.run(go())


def test_the_supervisor_starts_a_container_when_none_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(front, "SUPERVISE_EVERY", 0.1)
    monkeypatch.setattr(front, "RESTART_BACKOFF", 0.0)

    async def go():
        f, docker, port = await make(tmp_path)
        for _ in range(60):
            if docker.ups:
                break
            await asyncio.sleep(0.1)
        assert docker.ups >= 1, "nothing was started"
        for _ in range(60):  # then it forwards to what it started
            try:
                if (await asyncio.to_thread(get, port)).startswith("VERSION: restarted"):
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("never served the restarted container")
        await f.close()
    asyncio.run(go())


def test_the_control_socket_answers_swap_and_status_and_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(front.config, "STATE_DIR", tmp_path)

    async def go():
        inner = _free_port()
        docker = FakeDocker(inner)
        docker.loop = asyncio.get_running_loop()
        f = front.Front("ctl", docker, "127.0.0.1", 0, inner, drain=0.1)  # default socket path under STATE_DIR
        await f.start()
        await docker.launch("v1")
        await f.refresh_target()
        await docker.launch("v2")
        assert (f.control.stat().st_mode & 0o077) == 0
        assert await asyncio.to_thread(front.request, "ctl", "swap", 10) == "ok"
        assert '"swaps": 1' in await asyncio.to_thread(front.request, "ctl", "status", 5)
        assert (await asyncio.to_thread(front.request, "ctl", "nonsense", 5)).startswith("err:")
        await f.close()
        assert front.request("ctl") is None  # supervisor gone: caller falls back to a restart
    asyncio.run(go())


def test_rollover_reports_none_when_no_supervisor_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(front.config, "STATE_DIR", tmp_path)
    assert front.rollover("nobody", tmp_path) == (None, "")


def test_the_unit_for_a_front_app_can_import_hoist():
    from hoist import service
    unit = service.render_unit({"name": "w", "port": 1, "workdir": "/tmp", "cmd": front.front_command("w"),
                                "kind": "compose", "front": {"inner": 8000, "bind": "127.0.0.1"}})
    assert "-m hoist front w" in unit and "Environment=PYTHONPATH=" in unit


def test_a_request_that_arrives_while_the_container_is_still_starting_waits_then_succeeds(tmp_path):
    """The bug this pins: a connect that failed during startup cleared the target and the
    supervisor never recovered it (the container id was still valid, so it returned early)."""
    async def go():
        f, docker, port = await make(tmp_path)
        cid = await docker.launch("v1", healthy=False)       # listed and running, nothing accepting yet
        await f.refresh_target()
        request = asyncio.ensure_future(asyncio.to_thread(get, port))
        await asyncio.sleep(0.6)                              # the request is held, not refused
        assert not request.done()
        box = docker.boxes[cid]

        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\nVERSION: v1\n")
            await writer.drain()
            writer.close()

        box["server"] = await asyncio.start_server(handle, box["ip"], docker.inner)   # now it comes up
        assert (await asyncio.wait_for(request, 5)).startswith("VERSION: v1")
        assert await asyncio.to_thread(get, port)             # and it keeps working afterwards
        await f.close()
    asyncio.run(go())


def test_a_second_deploy_inside_the_drain_window_still_goes_live_with_no_failed_requests(tmp_path):
    """The bug this pins (found by benchmarking real deploys 6 s apart): while the previous old
    container was still draining, `--scale 2` found two containers and started nothing, so the
    'new' version was the stale old one. Deploys must supersede a draining container."""
    async def go():
        f, docker, port = await make(tmp_path, drain=30)      # drain far longer than the test
        await docker.launch("v1")
        await f.refresh_target()
        seen, failures, stop = [], [], asyncio.Event()

        async def hammer():
            while not stop.is_set():
                try:
                    seen.append(await asyncio.to_thread(get, port))
                except Exception as exc:  # noqa: BLE001
                    failures.append(repr(exc))
                await asyncio.sleep(0.005)

        task = asyncio.ensure_future(hammer())
        await asyncio.sleep(0.2)
        assert await f.swap(tmp_path / "r2") == "ok"          # v1 is now draining (30 s)
        await asyncio.sleep(0.2)
        assert await f.swap(tmp_path / "r3") == "ok"          # a second deploy right behind it
        await asyncio.sleep(0.3)
        stop.set()
        await task
        await f.close()
        assert failures == [], failures[:3]
        assert seen[-1] == "VERSION: new2", seen[-3:]          # the NEWEST version serves, not a stale one
        assert len(docker.scaled) == 2                         # each deploy really started its own container
        assert "v1-127.0.0.2" in docker.removed                # the first old one was superseded at once, not left draining
        assert f.target_id == "new2-127.0.0.4"                  # and the newest deploy is the one being served
    asyncio.run(go())


def test_a_failed_start_of_the_new_version_leaves_the_old_one_serving(tmp_path):
    async def go():
        f, docker, port = await make(tmp_path)
        await docker.launch("v1")
        await f.refresh_target()
        docker.scale_up = lambda release: (False, "no such image")
        reply = await f.swap(tmp_path / "r2")
        assert reply.startswith("err:") and "no such image" in reply
        assert await asyncio.to_thread(get, port) == "VERSION: v1"
        await f.close()
    asyncio.run(go())


def test_running_containers_come_from_the_engine_socket_without_spawning_anything(tmp_path, monkeypatch):
    """Spawning `docker` every few seconds per app cost 3 % of a core idle. The socket path must
    work on its own, and must not touch the CLI at all."""
    import json as _json
    import os as _os
    import socketserver
    import threading

    payload = [{"Id": "bbb", "Created": 200, "NetworkSettings": {"Networks": {"n": {"IPAddress": "10.0.0.3"}}}},
               {"Id": "aaa", "Created": 100, "NetworkSettings": {"Networks": {"n": {"IPAddress": "10.0.0.2"}}}}]
    seen = {}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            seen["request"] = self.rfile.readline().decode()
            body = _json.dumps(payload).encode()
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))

    sock = str(tmp_path / "engine.sock")
    server = socketserver.UnixStreamServer(sock, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(front.Docker, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned docker")))
    docker = front.Docker("hoist-w", lambda: tmp_path)
    docker.socket = sock
    try:
        rows = docker.running()
    finally:
        server.shutdown()
    assert [r["id"] for r in rows] == ["aaa", "bbb"] and rows[-1]["ip"] == "10.0.0.3"   # oldest first, newest last
    assert "com.docker.compose.project%3Dhoist-w" in seen["request"] or "hoist-w" in seen["request"]
