"""Work out how to run whatever the user pointed hoist at."""

from __future__ import annotations

import json
import re
import shutil
import socket
from pathlib import Path

# Static-site directories get served by Python's own http.server so that a
# folder of HTML works with no toolchain at all.
STATIC_SERVE = "{python} -m http.server $PORT --bind 127.0.0.1"


class DetectionError(Exception):
    pass


def port_is_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port(preferred: int | None = None) -> int:
    """Return a bindable localhost port, honouring `preferred` when free."""
    if preferred is not None:
        if port_is_free(preferred):
            return preferred
        raise DetectionError(f"port {preferred} is already in use")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_in_use(port: int) -> bool:
    """True when something is actually listening on the port."""
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def lan_ip() -> str:
    """Best-effort LAN address, for offline/captive-wifi demos."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1, no packets are sent
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def _npm_script(pkg: Path) -> str | None:
    try:
        scripts = json.loads(pkg.read_text()).get("scripts", {})
    except (json.JSONDecodeError, OSError):
        return None
    for candidate in ("start", "dev", "serve", "preview"):
        if candidate in scripts:
            return f"npm {candidate}" if candidate == "start" else f"npm run {candidate}"
    return None


def _procfile_web(procfile: Path) -> str | None:
    try:
        for line in procfile.read_text().splitlines():
            match = re.match(r"^\s*web\s*:\s*(.+)$", line)
            if match:
                return match.group(1).strip()
    except OSError:
        pass
    return None


def detect_command(directory: Path) -> tuple[str, str]:
    """Return (command, description) for running the project in `directory`.

    `$PORT` in the command is substituted with the allocated port at launch.
    """
    python = shutil.which("python3") or "python3"

    procfile = directory / "Procfile"
    if procfile.is_file():
        cmd = _procfile_web(procfile)
        if cmd:
            return cmd, "Procfile web process"

    pkg = directory / "package.json"
    if pkg.is_file():
        script = _npm_script(pkg)
        if script:
            return script, f"package.json ({script})"

    if (directory / "manage.py").is_file():
        return f"{python} manage.py runserver 127.0.0.1:$PORT", "Django"

    for entry in ("app.py", "main.py", "server.py", "wsgi.py"):
        if (directory / entry).is_file():
            return f"{python} {entry}", f"Python ({entry})"

    if (directory / "Cargo.toml").is_file():
        return "cargo run --release", "Cargo"

    if (directory / "go.mod").is_file():
        return "go run .", "Go module"

    if (directory / "docker-compose.yml").is_file() or (directory / "compose.yml").is_file():
        raise DetectionError(
            "found a compose file -- hoist manages plain processes, so start "
            "compose yourself then run: hoist adopt <name> --port <port>"
        )

    if (directory / "index.html").is_file():
        return STATIC_SERVE.format(python=python), "static site (index.html)"

    if any(directory.glob("*.html")):
        return STATIC_SERVE.format(python=python), "static files"

    raise DetectionError(
        "could not work out how to run this directory -- pass --cmd '<command>' "
        "(use $PORT where the port goes)"
    )


def slug(text: str) -> str:
    """Turn an arbitrary string into a DNS-safe label."""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned[:40] or "app"
