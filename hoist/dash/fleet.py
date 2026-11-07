"""Several machines from one dashboard.

Every machine runs its own hoist and its own `hoist dash`; this dashboard
keeps a list of the others (URL plus a token, stored 0600) and forwards API
calls to them server-side. Nothing new runs on the other machines, and no
token ever reaches the browser.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_lock = threading.Lock()


class FleetError(Exception):
    pass


def path() -> Path:
    return config.STATE_DIR / "servers.json"


def servers() -> dict[str, dict]:
    try:
        return json.loads(path().read_text()).get("servers", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    tmp = path().with_name(f".servers.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({"servers": data}, indent=2) + "\n")
    os.replace(tmp, path())


def _base(url: str) -> str:
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://[^/\s]+$", url):
        raise FleetError("use the dashboard's address, like https://dash.example.com or http://10.0.0.5:7457")
    return url


def add(name: str, url: str, token: str) -> dict:
    """Link a machine after checking its dashboard answers to the token."""
    sid = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")[:31]
    if not ID.match(sid or ""):
        raise FleetError("give the machine a short name")
    base = _base(url)
    token = token.strip()
    if "token=" in token:  # a whole sign-in link was pasted
        token = token.split("token=", 1)[1].split("&", 1)[0]
    info = probe(base, token)
    if info.get("error"):
        raise FleetError(f"could not reach that dashboard: {info['error']}")
    if info.get("role") != "owner":
        raise FleetError("link the other machine with its owner's link, so it can be managed from here")
    with _lock:
        everyone = servers()
        everyone[sid] = {"name": name.strip()[:40], "url": base, "token": token,
                         "added": config.now(), "host": info.get("host")}
        _save(everyone)
    return {"id": sid, "name": name.strip(), "url": base, "host": info.get("host")}


def remove(sid: str) -> bool:
    with _lock:
        everyone = servers()
        if everyone.pop(sid, None) is None:
            return False
        _save(everyone)
    return True


def request(server: dict, method: str, rest: str, body: bytes | None = None,
            stream: bool = False, timeout: float = 15.0):
    """Forward one API call. Returns the open response (caller reads/closes)."""
    headers = {"Authorization": f"Bearer {server['token']}", "User-Agent": "hoist-fleet"}
    if method != "GET":
        headers["X-Hoist"] = "1"
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{server['url']}/api/{rest}", data=body, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=None if stream else timeout)


def probe(base: str, token: str) -> dict:
    started = time.monotonic()
    try:
        with request({"url": base, "token": token}, "GET", "state", timeout=8) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return {"error": "the token was refused" if exc.code == 401 else f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(getattr(exc, "reason", exc))[:160]}
    data["latency_ms"] = round((time.monotonic() - started) * 1000)
    return data


def summary() -> list[dict]:
    """Every linked machine's health, fetched in parallel."""
    out: list[dict] = []
    threads = []
    for sid, srv in servers().items():
        row = {"id": sid, "name": srv["name"], "url": srv["url"]}
        out.append(row)

        def fill(row=row, srv=srv):
            info = probe(srv["url"], srv["token"])
            if info.get("error"):
                row.update(ok=False, error=info["error"])
                return
            apps = [a for a in info.get("apps", []) if a.get("kind") != "db"]
            broken = [a["name"] for a in apps if a.get("status") in ("down", "failed")
                      or (a.get("public") and a.get("probe") and not a["probe"].get("up"))]
            row.update(ok=True, host=info.get("host"), latency_ms=info.get("latency_ms"),
                       apps=len(apps), dbs=len(info.get("apps", [])) - len(apps), broken=broken,
                       system=info.get("system"), version=info.get("version"))
        thread = threading.Thread(target=fill, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(12)
    return out


__all__ = ["FleetError", "add", "remove", "request", "servers", "summary", "probe", "secrets"]
