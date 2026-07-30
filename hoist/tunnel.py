"""Cloudflare Tunnel ingress management.

The config file is edited as text rather than round-tripped through a YAML
parser, so hand-written rules keep their comments, ordering and formatting.
hoist only ever inserts or deletes whole entries that it marked itself.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "# hoist:"

CANDIDATES = [
    Path("/etc/cloudflared/config.yml"),
    Path("/etc/cloudflared/config.yaml"),
    Path.home() / ".cloudflared" / "config.yml",
    Path.home() / ".cloudflared" / "config.yaml",
]


class TunnelError(Exception):
    pass


def have_cloudflared() -> bool:
    return shutil.which("cloudflared") is not None


def find_config() -> Path:
    override = os.environ.get("HOIST_CF_CONFIG")
    if override:
        path = Path(override)
        if not path.is_file():
            raise TunnelError(f"HOIST_CF_CONFIG points at a missing file: {path}")
        return path
    for candidate in CANDIDATES:
        if candidate.is_file():
            return candidate
    raise TunnelError(
        "no cloudflared config found -- create a tunnel first "
        "(cloudflared tunnel create <name>) or set HOIST_CF_CONFIG"
    )


def tunnel_id(text: str) -> str | None:
    match = re.search(r"^tunnel:\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


# --- ingress block parsing ----------------------------------------------------


class Entry:
    def __init__(self, lines: list[str], managed: str | None = None) -> None:
        self.lines = lines
        self.managed = managed

    @property
    def hostname(self) -> str | None:
        for line in self.lines:
            match = re.match(r"\s*-?\s*hostname:\s*(\S+)", line)
            if match:
                return match.group(1).strip("\"'")
        return None

    @property
    def is_catchall(self) -> bool:
        return self.hostname is None

    @property
    def indent(self) -> str:
        for line in self.lines:
            match = re.match(r"^(\s*)-\s*\S", line)
            if match:
                return match.group(1) or "  "
        return "  "


def parse(text: str) -> tuple[list[str], list[Entry], list[str]]:
    """Split config text into (head, ingress entries, tail)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^ingress:\s*$", line):
            start = i
            break
    if start is None:
        raise TunnelError("cloudflared config has no top-level `ingress:` block")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line[0].isspace() and not line.lstrip().startswith("#"):
            end = i
            break

    head = lines[: start + 1]
    body = lines[start + 1 : end]
    tail = lines[end:]

    entries: list[Entry] = []
    current: list[str] | None = None
    managed: str | None = None
    pending: list[str] = []
    pending_managed: str | None = None

    def flush() -> None:
        nonlocal current, managed
        if current is not None:
            entries.append(Entry(current, managed))
        current, managed = None, None

    for line in body:
        stripped = line.strip()
        if re.match(r"^-(\s|$)", stripped):
            flush()
            current = pending + [line]
            managed = pending_managed
            pending, pending_managed = [], None
        elif stripped.startswith("#"):
            # A comment closes the entry above it and attaches to the one below,
            # which keeps `# hoist:<name>` bound to the rule it introduces.
            flush()
            pending.append(line)
            if stripped.startswith(MARKER):
                pending_managed = stripped[len(MARKER) :].strip()
        elif current is not None:
            current.append(line)
        else:
            pending.append(line)
    flush()
    return head, entries, pending + tail


def render(head: list[str], entries: list[Entry], tail: list[str]) -> str:
    out = list(head)
    for entry in entries:
        out.extend(entry.lines)
    out.extend(tail)
    return "\n".join(out).rstrip("\n") + "\n"


def make_entry(name: str, hostname: str, port: int, indent: str = "  ") -> Entry:
    return Entry(
        [
            f"{indent}{MARKER}{name}",
            f"{indent}- hostname: {hostname}",
            f"{indent}  service: http://localhost:{port}",
        ],
        managed=name,
    )


def upsert(text: str, name: str, hostname: str, port: int) -> str:
    """Add or replace hoist's rule for `name`, keeping the catch-all last."""
    head, entries, tail = parse(text)
    indent = next((e.indent for e in entries if not e.is_catchall), "  ")

    conflict = next(
        (e for e in entries if e.hostname == hostname and e.managed not in (None, name)),
        None,
    )
    if conflict is not None:
        raise TunnelError(f"{hostname} is already hoisted as '{conflict.managed}'")

    hand_written = next(
        (e for e in entries if e.hostname == hostname and e.managed is None), None
    )
    if hand_written is not None:
        raise TunnelError(
            f"{hostname} already has a hand-written ingress rule -- "
            "pick another name, or remove that rule first"
        )

    entries = [e for e in entries if e.managed != name]
    new = make_entry(name, hostname, port, indent)
    catchall_at = next((i for i, e in enumerate(entries) if e.is_catchall), len(entries))
    entries.insert(catchall_at, new)

    if not any(e.is_catchall for e in entries):
        entries.append(Entry([f"{indent}- service: http_status:404"]))
    return render(head, entries, tail)


def remove(text: str, name: str) -> tuple[str, bool]:
    head, entries, tail = parse(text)
    kept = [e for e in entries if e.managed != name]
    return render(head, kept, tail), len(kept) != len(entries)


def hostnames(text: str) -> list[str]:
    _, entries, _ = parse(text)
    return [e.hostname for e in entries if e.hostname]


def default_domain(text: str) -> str | None:
    """Guess the zone from hostnames already in the config."""
    for host in hostnames(text):
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
    return None


# --- privileged file + daemon operations --------------------------------------


def needs_sudo(path: Path) -> bool:
    return not os.access(path, os.W_OK)


def sudo_is_passwordless() -> bool:
    """Whether sudo will run without prompting (cached credentials or NOPASSWD)."""
    result = subprocess.run(
        ["sudo", "-n", "true"], capture_output=True, text=True
    )
    return result.returncode == 0


def passwordless_setup(path: Path, user: str) -> list[str]:
    """One-time commands that let hoist publish without a password prompt.

    cloudflared does not watch its config file, so applying new ingress rules
    means restarting it -- which is why root is involved at all.
    """
    return [
        f"sudo chown {user} {path}",
        f'echo "{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart cloudflared"'
        " | sudo tee /etc/sudoers.d/hoist",
        "sudo chmod 440 /etc/sudoers.d/hoist",
    ]


def sudo_hint(path: Path, stderr: str) -> str:
    """Turn a sudo failure into something the user can act on."""
    detail = (stderr or "").strip().splitlines()
    if any("terminal is required" in line or "password is required" in line
           for line in detail):
        return (
            f"sudo could not prompt for a password, so {path} was not modified. "
            "Run `sudo -v` first, then re-run this command."
        )
    return f"could not write {path}: {detail[-1] if detail else 'sudo failed'}"


def reaches_tunnel(url: str, expected: bytes, timeout: float = 12.0) -> tuple[bool, str]:
    """Check the public URL actually serves our app.

    A wildcard DNS record on the zone will happily answer for a hostname the
    tunnel was never told about, so "it returns a page" is not good enough --
    the body has to match what the app serves locally.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "hoist"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(len(expected) + 64)
            server = response.headers.get("server", "?")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} from {exc.headers.get('server', '?')}"
    except Exception as exc:  # DNS, TLS, timeout -- all just "not reachable yet"
        return False, str(exc)
    if expected and body[: len(expected)] != expected:
        return False, f"a different server answered (server: {server})"
    return True, server


def write_config(path: Path, text: str) -> Path:
    """Back up then write, escalating with sudo only when required."""
    # Backups live in hoist's own state directory, not beside the config: the
    # config may be user-writable while /etc/cloudflared is still root-owned,
    # so writing a sibling file would fail even though the edit itself works.
    backup_dir = Path(
        os.environ.get("HOIST_HOME", Path.home() / ".config" / "hoist")
    ) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.{int(time.time())}"
    if needs_sudo(path):
        copied = subprocess.run(
            ["sudo", "cp", str(path), str(backup)], capture_output=True, text=True
        )
        if copied.returncode != 0:
            raise TunnelError(sudo_hint(path, copied.stderr))
        result = subprocess.run(
            ["sudo", "tee", str(path)], input=text, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise TunnelError(sudo_hint(path, result.stderr))
    else:
        try:
            shutil.copy2(path, backup)
            path.write_text(text)
        except OSError as exc:
            raise TunnelError(f"could not write {path}: {exc}") from exc
    return backup


def restore(path: Path, backup: Path) -> None:
    if needs_sudo(path):
        subprocess.run(["sudo", "cp", str(backup), str(path)], check=False)
    else:
        shutil.copy2(backup, path)


def validate(path: Path) -> tuple[bool, str]:
    if not have_cloudflared():
        return True, "cloudflared not installed, skipped validation"
    # `--config` is a flag on `tunnel`, not on `ingress validate`. Put it after
    # the subcommand and cloudflared prints "Incorrect Usage" and still exits 0,
    # which silently turns this safety check into a no-op.
    result = subprocess.run(
        ["cloudflared", "tunnel", "--config", str(path), "ingress", "validate"],
        capture_output=True,
        text=True,
    )
    output = (result.stderr or result.stdout).strip()
    if "Incorrect Usage" in output or "flag provided but not defined" in output:
        raise TunnelError(f"cannot validate ingress config: {output.splitlines()[0]}")
    return result.returncode == 0, output


def route_dns(tunnel: str, hostname: str) -> tuple[bool, str]:
    """Point `hostname` at the tunnel. Idempotent; already-routed counts as ok."""
    result = subprocess.run(
        ["cloudflared", "tunnel", "route", "dns", tunnel, hostname],
        capture_output=True,
        text=True,
    )
    output = (result.stderr or result.stdout).strip()
    lowered = output.lower()
    if result.returncode == 0:
        return True, output
    if "already exists" in lowered or "record with that host" in lowered:
        return True, "DNS record already present"
    return False, output


def daemon_unit() -> tuple[str, bool] | None:
    """Return (unit, is_system) for the running cloudflared, if any."""
    system = subprocess.run(
        ["systemctl", "is-active", "cloudflared"], capture_output=True, text=True
    )
    if system.stdout.strip() == "active":
        return "cloudflared", True
    user = subprocess.run(
        ["systemctl", "--user", "is-active", "cloudflared"],
        capture_output=True,
        text=True,
    )
    if user.stdout.strip() == "active":
        return "cloudflared", False
    return None


def reload() -> tuple[bool, str]:
    """Restart cloudflared so new ingress rules take effect."""
    found = daemon_unit()
    if found is None:
        return False, "cloudflared does not appear to be running as a service"
    unit, is_system = found
    cmd = (
        ["sudo", "systemctl", "restart", unit]
        if is_system
        else ["systemctl", "--user", "restart", unit]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or "restart failed"
    return True, unit
