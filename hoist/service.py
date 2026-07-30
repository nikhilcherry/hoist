"""systemd --user unit management.

User units are deliberate: no sudo is needed to run the app itself, and
`systemctl --user` gives restart-on-crash and journald logs for free.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

UNIT_DIR = Path(
    os.environ.get("HOIST_UNIT_DIR", Path.home() / ".config" / "systemd" / "user")
)
PREFIX = "hoist-"


class ServiceError(Exception):
    pass


def unit_name(name: str) -> str:
    return f"{PREFIX}{name}.service"


def unit_path(name: str) -> Path:
    return UNIT_DIR / unit_name(name)


def have_systemd() -> bool:
    return shutil.which("systemctl") is not None and Path("/run/systemd/system").exists()


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, check=check
    )


def build_exec_start(cmd: str) -> str:
    """Turn a user command into a systemd-safe ExecStart line."""
    if any(ch in cmd for ch in "|&;<>$*?"):
        return f"/bin/sh -lc {shlex.quote(cmd)}"
    parts = shlex.split(cmd)
    if not parts:
        raise ServiceError("empty command")
    resolved = shutil.which(parts[0])
    if not resolved:
        # Let a login shell resolve it at start time (nvm, pyenv, cargo, ...).
        return f"/bin/sh -lc {shlex.quote(cmd)}"
    parts[0] = resolved
    return " ".join(shlex.quote(p) for p in parts)


def render_unit(app: dict) -> str:
    """Build the unit file text for `app`."""
    cmd = app["cmd"].replace("$PORT", str(app["port"]))
    exec_start = build_exec_start(cmd)
    env_lines = "".join(
        f"Environment={k}={v}\n" for k, v in sorted(app.get("env", {}).items())
    )
    return f"""[Unit]
Description=hoist: {app['name']}
Documentation=https://github.com/nikhilcherry/hoist
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={app['workdir']}
Environment=PORT={app['port']}
Environment=HOIST_APP={app['name']}
{env_lines}ExecStart={exec_start}
Restart=on-failure
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def write_unit(app: dict) -> Path:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    path = unit_path(app["name"])
    path.write_text(render_unit(app))
    _systemctl("daemon-reload")
    return path


def start(name: str) -> None:
    result = _systemctl("enable", "--now", unit_name(name))
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "systemctl enable --now failed")


def restart(name: str) -> None:
    result = _systemctl("restart", unit_name(name))
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "systemctl restart failed")


def stop_and_remove(name: str) -> None:
    _systemctl("disable", "--now", unit_name(name))
    unit_path(name).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    _systemctl("reset-failed", unit_name(name))


def status(name: str) -> str:
    """One-word state: active / failed / inactive / unknown."""
    return _systemctl("is-active", unit_name(name)).stdout.strip() or "unknown"


def logs(name: str, lines: int = 50, follow: bool = False) -> int:
    args = [
        "journalctl", "--user", "-u", unit_name(name),
        "-n", str(lines), "--no-pager",
    ]
    if follow:
        args.append("-f")
    return subprocess.run(args).returncode


def lingering_enabled() -> bool:
    """Whether user services survive logout."""
    user = os.environ.get("USER") or Path.home().name
    return (Path("/var/lib/systemd/linger") / user).exists()
