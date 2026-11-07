"""State file safety: concurrent writers, and never reading corruption as empty."""

from __future__ import annotations

import json
import multiprocessing
import threading

import pytest

from hoist import config


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "apps.json")
    return tmp_path


def _bump(n: int) -> None:
    for _ in range(n):
        with config.transaction() as state:
            state["apps"].setdefault("a", {"name": "a", "count": 0})
            state["apps"]["a"]["count"] += 1


def test_concurrent_processes_neither_corrupt_nor_lose_updates(state_dir):
    """The bug this pins: two sync timers fired by one push wrote through the
    same temp file, and apps.json came out as two documents run together."""
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_bump, args=(25,)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    data = json.loads((state_dir / "apps.json").read_text())
    assert data["apps"]["a"]["count"] == 100


def test_threads_writing_at_once_leave_valid_json(state_dir):
    def write(i):
        for _ in range(20):
            config.save({"version": 1, "domain": None, "apps": {f"x{i}": {"name": f"x{i}", "pad": "y" * (i * 300)}}})
    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    json.loads((state_dir / "apps.json").read_text())


def test_a_corrupt_file_falls_back_to_the_backup(state_dir):
    config.save({"version": 1, "domain": None, "apps": {"keep": {"name": "keep"}}})
    config.save({"version": 1, "domain": None, "apps": {"keep": {"name": "keep"}, "new": {"name": "new"}}})
    (state_dir / "apps.json").write_text('{"apps": {}} trailing junk')
    assert set(config.load()["apps"]) == {"keep"}


def test_a_corrupt_file_with_no_backup_is_an_error_not_an_empty_state(state_dir):
    (state_dir / "apps.json").write_text("{not json")
    with pytest.raises(config.StateError):
        config.load()
