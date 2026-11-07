"""Everything the dashboard shows, gathered off the request path.

A background sampler reads systemd, /proc and the tunnel config every few
seconds and probes each public URL every half minute, so a page load is a
dictionary lookup rather than a dozen subprocesses. History is kept in
memory only: an hour of samples, enough to see what a deploy did.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from .. import config, containers, detect, notify, service, source, tunnel

SAMPLE_EVERY = 3.0          # seconds between resource samples
PROBE_EVERY = 30.0          # seconds between public URL checks
KEEP_SAMPLES = 1200         # one hour at SAMPLE_EVERY
KEEP_PROBES = 120           # one hour at PROBE_EVERY

UNIT_PROPS = (
    "Id", "ActiveState", "SubState", "MainPID", "NRestarts",
    "ActiveEnterTimestamp", "MemoryCurrent", "CPUUsageNSec",
)


# --- low-level readers ----------------------------------------------------------


def _int(value: str | None) -> int | None:
    try:
        number = int(value or "")
    except ValueError:
        return None
    # systemd reports "not tracked" as UINT64_MAX
    return None if number >= 2**63 else number


def unit_props(units: list[str]) -> dict[str, dict[str, str]]:
    """`systemctl --user show` for many units in one call."""
    if not units:
        return {}
    result = subprocess.run(
        ["systemctl", "--user", "show", "-p", ",".join(UNIT_PROPS), *units],
        capture_output=True, text=True,
    )
    out: dict[str, dict[str, str]] = {}
    for block in result.stdout.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id"):
            out[props["Id"]] = props
    return out


def _cpu_times() -> tuple[int, int]:
    """(busy, total) jiffies from the first line of /proc/stat."""
    with open("/proc/stat") as fh:
        fields = [int(x) for x in fh.readline().split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    total = sum(fields[:8])
    return total - idle, total


def net_bytes() -> tuple[int, int]:
    """Total received/sent bytes on real interfaces (not loopback or Docker's)."""
    rx = tx = 0
    with open("/proc/net/dev") as fh:
        for line in fh.readlines()[2:]:
            name, _, data = line.partition(":")
            name = name.strip()
            if name == "lo" or name.startswith(("veth", "docker", "br-", "virbr")):
                continue
            fields = data.split()
            rx += int(fields[0])
            tx += int(fields[8])
    return rx, tx


def meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) * 1024
    return info


def uptime_seconds() -> float:
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


def _since(timestamp: str) -> float | None:
    """Parse systemd's 'Sat 2026-11-07 16:11:56 IST' into epoch seconds."""
    if not timestamp or timestamp == "n/a":
        return None
    parts = timestamp.split()
    try:  # systemd prints local time; drop the weekday and zone name
        parsed = time.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError):
        return None
    return time.mktime(parsed)


CGROUP = Path("/sys/fs/cgroup/system.slice")


def container_usage(apps: dict) -> dict[str, dict]:
    """Memory and CPU of the containers behind database and Compose apps.

    Their systemd unit only holds the docker client, so its own figures are
    the client's; the real numbers are in each container's cgroup.
    """
    wanted = {n for n, a in apps.items() if a.get("kind") in ("db", "compose")}
    if not wanted or not containers.available():
        return {}
    listing = containers.run("ps", "--no-trunc", "--format",
                             '{{.ID}}|{{.Names}}|{{.Label "com.docker.compose.project"}}', timeout=10)
    usage: dict[str, dict] = {}
    for line in listing.stdout.splitlines():
        cid, _, rest = line.partition("|")
        cname, _, project = rest.partition("|")
        owner = None
        for name in wanted:
            if cname == containers.container_name(name) or project == containers.compose_project(name):
                owner = name
        if owner is None:
            continue
        scope = CGROUP / f"docker-{cid}.scope"
        try:
            memory = int((scope / "memory.current").read_text())
            cpu_us = next(int(l.split()[1]) for l in (scope / "cpu.stat").read_text().splitlines()
                          if l.startswith("usage_usec"))
        except (OSError, ValueError, StopIteration):
            continue
        entry = usage.setdefault(owner, {"memory": 0, "cpu_ns": 0, "containers": 0})
        entry["memory"] += memory
        entry["cpu_ns"] += cpu_us * 1000
        entry["containers"] += 1
    return usage


def routes() -> dict:
    """Every ingress rule in the tunnel config, hoist's own and hand-written."""
    try:
        path = tunnel.find_config()
        text = path.read_text()
    except (tunnel.TunnelError, OSError) as exc:
        return {"config": None, "error": str(exc), "rules": [], "tunnel": None, "domain": None}
    _, entries, _ = tunnel.parse(text)
    rules = []
    for entry in entries:
        service_line = next((ln.split("service:", 1)[1].strip() for ln in entry.lines
                             if "service:" in ln), "")
        rules.append({
            "hostname": entry.hostname,
            "service": service_line,
            "managed": entry.managed,
            "catchall": entry.is_catchall,
        })
    return {"config": str(path), "rules": rules, "tunnel": tunnel.tunnel_id(text),
            "domain": tunnel.default_domain(text), "error": None}


def probe(url: str, timeout: float = 8.0) -> dict:
    """One public request: status code and time to first byte."""
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "hoist-dash/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:  # DNS, TLS, timeout
        return {"t": time.time(), "code": 0, "ms": None, "up": False,
                "error": str(getattr(exc, "reason", exc))[:120]}
    ms = round((time.monotonic() - started) * 1000)
    # 4xx is the app answering (a 404 at / is normal for an API); 5xx and
    # Cloudflare's 52x/530 mean the origin is not there.
    return {"t": time.time(), "code": code, "ms": ms, "up": code < 500, "error": None}


def journal(unit: str, lines: int = 300) -> list[dict]:
    result = subprocess.run(
        ["journalctl", "--user", "-u", unit, "-n", str(lines), "--no-pager", "-o", "json",
         "--output-fields=MESSAGE,PRIORITY,_PID"],
        capture_output=True, text=True, errors="replace",
    )
    return [entry for entry in (parse_journal_line(ln) for ln in result.stdout.splitlines()) if entry]


def parse_journal_line(line: str) -> dict | None:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    message = raw.get("MESSAGE", "")
    if isinstance(message, list):  # journald stores non-UTF-8 as a byte array
        message = bytes(message).decode("utf-8", "replace")
    try:
        ts = int(raw.get("__REALTIME_TIMESTAMP", "0")) / 1e6
    except ValueError:
        ts = 0.0
    try:
        priority = int(raw.get("PRIORITY", 6))
    except (TypeError, ValueError):
        priority = 6
    return {"t": ts, "msg": message, "p": priority}


# --- the sampler -------------------------------------------------------------------


class Monitor(threading.Thread):
    """Keeps a fresh snapshot and an hour of history, in memory."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="hoist-dash-monitor")
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.system_series: deque = deque(maxlen=KEEP_SAMPLES)
        self.app_series: dict[str, deque] = {}
        self.probes: dict[str, deque] = {}
        self._last_cpu: tuple[int, int] | None = None
        self._last_net: tuple[int, int, float] | None = None
        self._last_unit_cpu: dict[str, tuple[int, float]] = {}
        self._last_probe = 0.0
        self._down: dict[str, int] = {}   # consecutive failed public checks per app
        self._alerted: set[str] = set()
        self.snapshot: dict = {}
        self.started = time.time()
        self.refresh()

    # Call after an action so the next poll reflects it without waiting.
    def nudge(self) -> None:
        self.wake.set()

    def run(self) -> None:
        while True:
            self.wake.wait(SAMPLE_EVERY)
            self.wake.clear()
            try:
                self.refresh()
            except Exception as exc:  # never let one bad read kill the sampler
                print(f"hoist dash: sampler error: {exc}")

    def refresh(self) -> None:
        state = config.load()
        apps = state.get("apps", {})
        now = time.time()

        units = [service.unit_name(n) for n, a in apps.items() if not a.get("adopted")]
        timers = [f"{source.sync_unit_base(n)}.timer" for n, a in apps.items() if a.get("repo")]
        props = unit_props(units + timers)

        busy, total = _cpu_times()
        cpu = None
        if self._last_cpu and total > self._last_cpu[1]:
            cpu = 100.0 * (busy - self._last_cpu[0]) / (total - self._last_cpu[1])
        self._last_cpu = (busy, total)
        rx, tx = net_bytes()
        rx_rate = tx_rate = None
        if self._last_net and now > self._last_net[2]:
            span = now - self._last_net[2]
            rx_rate = max(0, (rx - self._last_net[0]) / span)
            tx_rate = max(0, (tx - self._last_net[1]) / span)
        self._last_net = (rx, tx, now)
        mem = meminfo()
        mem_used = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
        disk = shutil.disk_usage(Path.home())
        load = os.getloadavg()

        boxed = container_usage(apps)
        rows = []
        for name in sorted(apps):
            app = apps[name]
            unit = dict(props.get(service.unit_name(name), {}))
            if name in boxed:  # report the containers, not the docker client
                unit["MemoryCurrent"] = str(boxed[name]["memory"])
                unit["CPUUsageNSec"] = str(boxed[name]["cpu_ns"])
            listening = detect.port_in_use(int(app["port"]))
            active = unit.get("ActiveState", "inactive")
            if app.get("adopted"):
                status = "up" if listening else "down"
            elif app.get("stopped"):
                status = "stopped"
            elif active == "active" and listening:
                status = "up"
            elif active in ("active", "activating"):
                status = "starting"
            elif active == "failed":
                status = "failed"
            else:
                status = "down"

            memory = _int(unit.get("MemoryCurrent"))
            cpu_ns = _int(unit.get("CPUUsageNSec"))
            app_cpu = None
            if cpu_ns is not None:
                last = self._last_unit_cpu.get(name)
                if last and now > last[1] and cpu_ns >= last[0]:
                    app_cpu = 100.0 * (cpu_ns - last[0]) / ((now - last[1]) * 1e9)
                self._last_unit_cpu[name] = (cpu_ns, now)
            series = self.app_series.setdefault(name, deque(maxlen=KEEP_SAMPLES))
            series.append((round(now), round(app_cpu, 2) if app_cpu is not None else None,
                           memory))

            repo = app.get("repo")
            timer = props.get(f"{source.sync_unit_base(name)}.timer", {}) if repo else {}
            if app.get("kind") == "db":
                url = f"127.0.0.1:{app['port']}"
            else:
                url = (f"https://{app['hostname']}" if app.get("tunnel") and app.get("hostname")
                       else f"http://{app.get('lan_ip', '127.0.0.1')}:{app['port']}")
            probes = list(self.probes.get(name, []))
            rows.append({
                "name": name,
                "status": status,
                "port": app["port"],
                "url": url,
                "public": bool(app.get("tunnel")),
                "hostname": app.get("hostname"),
                "adopted": bool(app.get("adopted")),
                "stopped": bool(app.get("stopped")),
                "workdir": app.get("workdir"),
                "cmd": app.get("cmd"),
                "env_keys": sorted((app.get("env") or {}).keys()),
                "limits": app.get("limits") or {},
                "kind": app.get("kind") or "app",
                "stage": app.get("stage") or ("preview" if app.get("preview_of") else "production"),
                "containers": boxed.get(name, {}).get("containers"),
                "preview_of": app.get("preview_of"),
                "dockerfile": bool(app.get("dockerfile")),
                "template": app.get("template"),
                "db": ({k: app["db"].get(k) for k in ("engine", "url", "env_var", "volume", "image", "database",
                                                      "offsite", "daily_backup")}
                       if app.get("kind") == "db" else None),
                "created": app.get("created"),
                "updated": app.get("updated"),
                "pid": _int(unit.get("MainPID")) or None,
                "restarts": _int(unit.get("NRestarts")) or 0,
                "since": _since(unit.get("ActiveEnterTimestamp", "")) if active == "active" else None,
                "memory": memory,
                "cpu": round(app_cpu, 1) if app_cpu is not None else None,
                "repo": _repo_summary(repo, timer) if repo else None,
                "probe": probes[-1] if probes else None,
                "uptime": _uptime(probes),
            })

        snapshot = {
            "t": now,
            "host": socket.gethostname(),
            "domain": state.get("domain"),
            "apps": rows,
            "system": {
                "cpu": round(cpu, 1) if cpu is not None else None,
                "cores": os.cpu_count() or 1,
                "load": [round(x, 2) for x in load],
                "mem_total": mem.get("MemTotal", 0),
                "mem_used": mem_used,
                "swap_total": mem.get("SwapTotal", 0),
                "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0),
                "disk_total": disk.total,
                "disk_used": disk.used,
                "uptime": uptime_seconds(),
                "cloudflared": bool(tunnel.daemon_unit()),
                "net_rx": round(rx_rate) if rx_rate is not None else None,
                "net_tx": round(tx_rate) if tx_rate is not None else None,
            },
            "routes": routes(),
        }
        self.system_series.append((round(now), snapshot["system"]["cpu"], mem_used,
                                   snapshot["system"]["net_rx"], snapshot["system"]["net_tx"]))
        with self.lock:
            self.snapshot = snapshot

        if now - self._last_probe >= PROBE_EVERY:
            self._last_probe = now
            threading.Thread(target=self._probe_all, args=(rows, snapshot["routes"]),
                             daemon=True).start()

    def _probe_all(self, rows: list[dict], route_info: dict) -> None:
        targets = {row["name"]: row["url"] for row in rows if row["public"]}
        # Hand-written routes (n8n, an API...) are watched too, keyed by hostname.
        for rule in route_info.get("rules", []):
            host = rule.get("hostname")
            if host and not rule.get("managed"):
                targets[f"route:{host}"] = f"https://{host}"
        stopped = {row["name"] for row in rows if row["stopped"]}
        for key, url in targets.items():
            result = probe(url)
            with self.lock:
                self.probes.setdefault(key, deque(maxlen=KEEP_PROBES)).append(result)
            if key.startswith("route:") or key in stopped:
                continue
            self._watch_downtime(key, url, result)

    def _watch_downtime(self, name: str, url: str, result: dict) -> None:
        """Alert after two failed checks in a row (one minute), and on recovery."""
        if result["up"]:
            self._down[name] = 0
            if name in self._alerted:
                self._alerted.discard(name)
                notify.send(f"{name} is answering again", f"{url} — {result['code']} in {result['ms']} ms",
                            level="ok", app=name, wait=False)
            return
        self._down[name] = self._down.get(name, 0) + 1
        if self._down[name] == 2 and name not in self._alerted:
            self._alerted.add(name)
            why = f"answers {result['code']}" if result["code"] else (result.get("error") or "unreachable")
            notify.send(f"{name} stopped answering", f"{url} {why} for the last minute.",
                        level="bad", app=name, wait=False)

    def probes_for(self, key: str) -> list[dict]:
        with self.lock:
            return list(self.probes.get(key, []))

    def get(self) -> dict:
        with self.lock:
            snap = dict(self.snapshot)
        # attach the latest probe of hand-written routes
        rules = []
        for rule in snap.get("routes", {}).get("rules", []):
            rule = dict(rule)
            if rule.get("hostname") and not rule.get("managed"):
                history = self.probes_for(f"route:{rule['hostname']}")
                rule["probe"] = history[-1] if history else None
                rule["uptime"] = _uptime(history)
            rules.append(rule)
        if "routes" in snap:
            snap["routes"] = dict(snap["routes"], rules=rules)
        return snap

    def series(self, name: str | None = None) -> list:
        with self.lock:
            if name is None:
                return list(self.system_series)
            return list(self.app_series.get(name, []))


def _uptime(probes: list[dict]) -> float | None:
    if not probes:
        return None
    return round(100.0 * sum(1 for p in probes if p["up"]) / len(probes), 1)


def _repo_summary(repo: dict, timer: dict) -> dict:
    return {
        "url": repo.get("url"),
        "branch": repo.get("branch"),
        "sha": repo.get("sha"),
        "commit": repo.get("commit"),
        "deployed": repo.get("deployed"),
        "failed_sha": repo.get("failed_sha"),
        "paused": bool(repo.get("paused")),
        "previews": bool(repo.get("previews")),
        "webhook": bool(repo.get("webhook_secret")),
        "preview_failed": repo.get("preview_failed") or {},
        "build": repo.get("build", []),
        "watching": timer.get("ActiveState") == "active",
        "last": (repo.get("history") or [None])[0],
    }
