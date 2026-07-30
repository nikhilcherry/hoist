"""Persistent state for hoisted apps."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.environ.get("HOIST_HOME", Path.home() / ".config" / "hoist"))
STATE_FILE = STATE_DIR / "apps.json"

_DEFAULT: dict[str, Any] = {"version": 1, "domain": None, "apps": {}}


def load() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"version": 1, "domain": None, "apps": {}}
    try:
        data = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "domain": None, "apps": {}}
    for key, value in _DEFAULT.items():
        data.setdefault(key, value)
    return data


def save(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(STATE_FILE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_app(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    return state["apps"].get(name)


def put_app(state: dict[str, Any], app: dict[str, Any]) -> None:
    state["apps"][app["name"]] = app


def drop_app(state: dict[str, Any], name: str) -> None:
    state["apps"].pop(name, None)
