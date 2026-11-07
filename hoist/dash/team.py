"""Teammates and the audit log.

The token printed by `hoist dash` belongs to the owner. Each teammate gets
their own sign-in link (`hoist dash --add-user shreyas`), stored only as a
SHA-256 hash, so removing one person never signs anyone else out, and every
action in the dashboard is written to an append-only log with who did it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .. import config

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
OWNER = "owner"
ROLES = ("member", "viewer")  # member: deploy and operate; viewer: read only


def role_of(name: str | None) -> str | None:
    if name is None:
        return None
    if name == OWNER:
        return OWNER
    return users().get(name, {}).get("role", "member")


_lock = threading.Lock()


class TeamError(Exception):
    pass


def users_path() -> Path:
    return config.STATE_DIR / "dash-users.json"


def audit_path() -> Path:
    return config.STATE_DIR / "audit.jsonl"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def users() -> dict[str, dict]:
    try:
        return json.loads(users_path().read_text()).get("users", {})
    except (OSError, json.JSONDecodeError):
        return {}


def add(name: str, role: str = "member") -> str:
    name = name.strip().lower()
    if role not in ROLES:
        raise TeamError(f"role must be one of {', '.join(ROLES)}")
    if not NAME.match(name) or name == OWNER:
        raise TeamError("a name is lowercase letters, digits, - or _, up to 24, and not 'owner'")
    with _lock:
        everyone = users()
        if name in everyone:
            raise TeamError(f"{name} already has access — remove them first to issue a new link")
        token = secrets.token_urlsafe(24)
        everyone[name] = {"hash": _hash(token), "added": config.now(), "role": role}
        _write_private(users_path(), json.dumps({"users": everyone}, indent=2) + "\n")
    return token


def set_role(name: str, role: str) -> None:
    if role not in ROLES:
        raise TeamError(f"role must be one of {', '.join(ROLES)}")
    with _lock:
        everyone = users()
        if name not in everyone:
            raise TeamError(f"no teammate called {name!r}")
        everyone[name]["role"] = role
        _write_private(users_path(), json.dumps({"users": everyone}, indent=2) + "\n")


def remove(name: str) -> bool:
    with _lock:
        everyone = users()
        if everyone.pop(name, None) is None:
            return False
        _write_private(users_path(), json.dumps({"users": everyone}, indent=2) + "\n")
    return True


def identify(token: str, owner_token: str) -> str | None:
    """Who a token belongs to, compared in constant time; None if nobody."""
    if not token:
        return None
    if hmac.compare_digest(token, owner_token):
        return OWNER
    digest = _hash(token)
    match = None
    for name, info in users().items():
        if hmac.compare_digest(digest, info.get("hash", "")):
            match = name
    return match


def audit(user: str, action: str, target: str | None = None, detail: str = "") -> None:
    entry = {"t": time.time(), "user": user, "action": action, "target": target, "detail": detail[:300]}
    with _lock:
        audit_path().parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path(), "a") as fh:
            fh.write(json.dumps(entry) + "\n")


def audit_tail(limit: int = 200) -> list[dict]:
    try:
        lines = audit_path().read_text().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
