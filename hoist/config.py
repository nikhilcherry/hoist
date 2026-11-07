"""Persistent state for hoisted apps.

Several hoist processes can touch this file at once: one sync timer per app
(two apps on the same repo fire on the same push), the dashboard, and the CLI.
So every write goes to a private temp file and is renamed into place, the
previous good copy is kept as a backup, and read-modify-write sections take an
exclusive lock via `transaction()`. A file that fails to parse is never read
as "no apps": that would let the next save wipe every app.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATE_DIR = Path(os.environ.get("HOIST_HOME", Path.home() / ".config" / "hoist"))
STATE_FILE = STATE_DIR / "apps.json"

_DEFAULT: dict[str, Any] = {"version": 1, "domain": None, "apps": {}}
_local = threading.local()


class StateError(Exception):
    pass


def _fresh() -> dict[str, Any]:
    return {"version": 1, "domain": None, "apps": {}}


def _backup() -> Path:
    return STATE_FILE.with_name(STATE_FILE.name + ".bak")


def _parse(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise json.JSONDecodeError("state is not an object", "", 0)
    for key, value in _DEFAULT.items():
        data.setdefault(key, value if not isinstance(value, dict) else dict(value))
    return data


def load() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _fresh()
    try:
        return _parse(STATE_FILE)
    except (json.JSONDecodeError, OSError) as exc:
        backup = _backup()
        if backup.exists():
            try:
                return _parse(backup)
            except (json.JSONDecodeError, OSError):
                pass
        raise StateError(
            f"{STATE_FILE} is unreadable ({exc}) and there is no good backup; "
            "fix or move it aside before running hoist again"
        ) from exc


def save(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w") as fh:
        fh.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    with _lock():
        if STATE_FILE.exists():
            try:
                _parse(STATE_FILE)  # only a good copy becomes the backup
                shutil.copy2(STATE_FILE, _backup())
            except (json.JSONDecodeError, OSError):
                pass
        os.replace(tmp, STATE_FILE)


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    """An exclusive, re-entrant (per thread) lock on the state directory."""
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(STATE_DIR / ".lock", "a+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    _local.depth = 1
    try:
        yield
    finally:
        _local.depth = 0
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


@contextlib.contextmanager
def transaction() -> Iterator[dict[str, Any]]:
    """Load, let the caller change, and save -- with no other writer in between."""
    with _lock():
        state = load()
        yield state
        save(state)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_app(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    return state["apps"].get(name)


def put_app(state: dict[str, Any], app: dict[str, Any]) -> None:
    state["apps"][app["name"]] = app


def drop_app(state: dict[str, Any], name: str) -> None:
    state["apps"].pop(name, None)
