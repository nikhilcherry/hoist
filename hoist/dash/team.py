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
ROLES = ("member", "viewer", "custom")

# What a person can be allowed to do. "view" is implied for everyone; the rest
# are granted per person (a role is just a preset) and optionally limited to a
# list of apps. The owner alone manages people, machines, terminals and commands.
STAGES = ("production", "staging", "development", "preview")
DEFAULT_STAGE = "production"

CAPS = {
    "create": "Create apps, databases and ready-made apps",
    "deploy": "Redeploy, roll back, set up webhooks",
    "operate": "Restart, stop, start, pause auto-deploy, branch previews",
    "config": "Edit environment variables, resource limits, links and notifications",
    "backups": "Back up, restore and schedule database and volume backups",
    "delete": "Take apps down",
    "terminal": "Open a terminal and run commands inside an app",
    "secrets": "See environment values, database passwords and download backups",
}
# A shell inside an app is far more powerful than restarting it, so the built-in
# member role does not include it: grant "terminal" deliberately, per person.
PRESETS = {"member": set(CAPS) - {"terminal"}, "viewer": set()}
ACTION_CAP = {
    "redeploy": "deploy", "rollback": "deploy", "webhook": "deploy", "stage": "config",
    "exec": "terminal",
    "restart": "operate", "stop": "operate", "start": "operate", "pause": "operate",
    "resume": "operate", "previews": "operate",
    "env": "config", "limits": "config", "link": "config",
    "backup": "backups", "restore": "backups", "backup-schedule": "backups",
    "delete": "delete",
}


def access(name: str | None) -> dict | None:
    """What this person may do: {"role", "caps": set, "apps": None|set}. None = not a person."""
    if name is None:
        return None
    if name == OWNER:
        return {"role": OWNER, "caps": set(CAPS), "apps": None, "stages": None, "owner": True}
    info = users().get(name)
    if info is None:
        return None
    role = info.get("role", "member")
    if role == "custom":
        caps = {c for c in info.get("can", []) if c in CAPS}
        apps = set(info.get("apps") or []) or None
        stages = set(info.get("stages") or []) or None
    else:
        caps = set(PRESETS.get(role, set()))
        apps = stages = None
    return {"role": role, "caps": caps, "apps": apps, "stages": stages, "owner": False}


def stage_of(app: str) -> str:
    """The environment an app belongs to (apps nobody labelled are production)."""
    a = config.get_app(config.load(), app) or {}
    return a.get("stage") or ("preview" if a.get("preview_of") else DEFAULT_STAGE)


def _in_scope(a: dict, app: str) -> bool:
    if a["apps"] is not None and app not in a["apps"]:
        return False
    return a["stages"] is None or stage_of(app) in a["stages"]


def role_of(name: str | None) -> str | None:
    a = access(name)
    return a["role"] if a else None


def allows(name: str | None, cap: str, app: str | None = None) -> bool:
    """May `name` use capability `cap` (on `app`, or on the whole machine if None)?"""
    a = access(name)
    if a is None:
        return False
    if a["owner"]:
        return True
    if cap == "view":
        return app is None or _in_scope(a, app)
    if cap not in a["caps"]:
        return False
    if a["apps"] is None and a["stages"] is None:
        return True
    return app is not None and _in_scope(a, app)


def scope(name: str | None) -> set[str] | None:
    """The apps this person can see, or None for all of them."""
    a = access(name)
    if a is None or (a["apps"] is None and a["stages"] is None):
        return None
    return {n for n in config.load().get("apps", {}) if _in_scope(a, n)}


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


def _custom(role: str, can: list[str] | None, apps: list[str] | None,
            stages: list[str] | None = None) -> dict:
    if role not in ROLES:
        raise TeamError(f"role must be one of {', '.join(ROLES)}")
    if role != "custom":
        return {"role": role}
    unknown = [c for c in (can or []) if c not in CAPS]
    if unknown:
        raise TeamError(f"unknown permission {unknown[0]!r}; choose from {', '.join(CAPS)}")
    can = list(can or [])
    apps = [a for a in (apps or []) if re.match(r"^[a-z0-9][a-z0-9-]{0,39}$", a)]
    bad = [s for s in (stages or []) if s not in STAGES]
    if bad:
        raise TeamError(f"unknown environment {bad[0]!r}; choose from {', '.join(STAGES)}")
    return {"role": "custom", "can": sorted(set(can)), "apps": sorted(set(apps)),
            "stages": sorted(set(stages or []))}


def add(name: str, role: str = "member", can: list[str] | None = None, apps: list[str] | None = None,
        stages: list[str] | None = None) -> str:
    name = name.strip().lower()
    entry = _custom(role, can, apps, stages)
    if not NAME.match(name) or name == OWNER:
        raise TeamError("a name is lowercase letters, digits, - or _, up to 24, and not 'owner'")
    with _lock:
        everyone = users()
        if name in everyone:
            raise TeamError(f"{name} already has access — remove them first to issue a new link")
        token = secrets.token_urlsafe(24)
        everyone[name] = {"hash": _hash(token), "added": config.now(), **entry}
        _write_private(users_path(), json.dumps({"users": everyone}, indent=2) + "\n")
    return token


def set_role(name: str, role: str, can: list[str] | None = None, apps: list[str] | None = None,
             stages: list[str] | None = None) -> None:
    entry = _custom(role, can, apps, stages)
    with _lock:
        everyone = users()
        if name not in everyone:
            raise TeamError(f"no teammate called {name!r}")
        keep = {k: v for k, v in everyone[name].items() if k in ("hash", "added")}
        everyone[name] = {**keep, **entry}
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
