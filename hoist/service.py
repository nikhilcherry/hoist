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


def systemctl(*args: str) -> subprocess.CompletedProcess:
    """`systemctl --user`, for modules that manage their own units."""
    return _systemctl(*args)


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


def env_line(key: str, value: str) -> str:
    """One `Environment=` line that systemd reads back as exactly key=value.

    `%` starts a unit specifier and must be doubled; spaces, quotes and
    backslashes need the double-quoted form.
    """
    value = str(value).replace("%", "%%")
    if any(ch in value for ch in " \t\"'\\") or value == "":
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{key}={escaped}"\n'
    return f"Environment={key}={value}\n"


LIMIT_MEMORY = r"^[1-9][0-9]{0,5}[KMG]$"
LIMIT_CPU = r"^[1-9][0-9]{0,3}%$"


def _high(memory: str) -> str:
    number, unit = int(memory[:-1]), memory[-1]
    return f"{max(1, number * 9 // 10)}{unit}"


def render_unit(app: dict) -> str:
    """Build the unit file text for `app`."""
    cmd = app["cmd"].replace("$PORT", str(app["port"]))
    exec_start = build_exec_start(cmd)
    limits = {} if app.get("kind") in ("db", "compose") else (app.get("limits") or {})
    limit_lines = ""
    if limits.get("memory"):
        # MemoryHigh throttles first, so a leak slows down before it is killed.
        limit_lines += f"MemoryMax={limits['memory']}\nMemoryHigh={_high(limits['memory'])}\n"
    if limits.get("cpu"):
        limit_lines += f"CPUQuota={limits['cpu']}\n"
    env_lines = "".join(
        env_line(k, v) for k, v in sorted(app.get("env", {}).items())
    )
    if app.get("front"):
        # `hoist front` must import this very hoist, installed or from a checkout.
        env_lines += f"Environment=PYTHONPATH={Path(__file__).resolve().parent.parent}\n"
        if os.environ.get("HOIST_HOME"):
            env_lines += f"Environment=HOIST_HOME={os.environ['HOIST_HOME']}\n"
    # Container apps clear a leftover container first and get longer to stop.
    hook_lines = "".join(f"ExecStartPre={line}\n" for line in app.get("pre", []))
    if app.get("kind") in ("db", "compose"):
        hook_lines += "TimeoutStopSec=45\n"
    return f"""[Unit]
Description=hoist: {app['name']}
Documentation=https://github.com/nikhilcherry/hoist
After=network-online.target
Wants=network-online.target
# No start rate limit: an app that crash-loops while you are still fixing it
# must keep retrying, not land in `failed` and stay there until reset-failed.
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={app['workdir']}
Environment=PORT={app['port']}
Environment=HOIST_APP={app['name']}
{env_lines}{limit_lines}{hook_lines}ExecStart={exec_start}
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
    # The unit carries the app's environment (API keys, database passwords),
    # so it is readable by this user only.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(render_unit(app))
    os.chmod(path, 0o600)
    _systemctl("daemon-reload")
    return path


def start(name: str) -> None:
    # Clear any earlier failure so a fixed app starts instead of being refused.
    _systemctl("reset-failed", unit_name(name))
    result = _systemctl("enable", "--now", unit_name(name))
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "systemctl enable --now failed")


def restart(name: str) -> None:
    _systemctl("reset-failed", unit_name(name))
    result = _systemctl("restart", unit_name(name))
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "systemctl restart failed")


def n_restarts(name: str) -> int:
    """How many times systemd has auto-restarted the unit since reset-failed.

    With the start rate limit disabled a crash-looping unit never settles into
    `failed`, so this is what tells us the app is not coming up.
    """
    result = _systemctl("show", "-p", "NRestarts", "--value", unit_name(name))
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def recent_logs(name: str, lines: int = 12) -> str:
    """Last few journal lines, for reporting a failed start inline."""
    result = subprocess.run(
        ["journalctl", "--user", "-u", unit_name(name), "-n", str(lines),
         "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def stop_and_remove(name: str) -> None:
    _systemctl("disable", "--now", unit_name(name))
    unit_path(name).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    _systemctl("reset-failed", unit_name(name))


def status(name: str) -> str:
    """One-word state: active / failed / inactive / unknown."""
    return _systemctl("is-active", unit_name(name)).stdout.strip() or "unknown"


def logs(name: str, lines: int = 50, follow: bool = False, unit: str | None = None) -> int:
    args = [
        "journalctl", "--user", "-u", unit or unit_name(name),
        "-n", str(lines), "--no-pager",
    ]
    if follow:
        args.append("-f")
    return subprocess.run(args).returncode


def lingering_enabled() -> bool:
    """Whether user services survive logout."""
    user = os.environ.get("USER") or Path.home().name
    return (Path("/var/lib/systemd/linger") / user).exists()
