"""Keep every test away from the real machine.

Nothing a test does may touch ~/.config/hoist (apps, tokens, teammates, audit
log, backups) or the real systemd user directory, even if the test forgets to
set up its own sandbox.
"""

import pytest

from hoist import config, service, source


@pytest.fixture(autouse=True)
def _isolated_machine(tmp_path, monkeypatch):
    state = tmp_path / "hoist-state"
    monkeypatch.setattr(config, "STATE_DIR", state)
    monkeypatch.setattr(config, "STATE_FILE", state / "apps.json")
    monkeypatch.setattr(source, "REPO_DIR", state / "repos")
    monkeypatch.setattr(service, "UNIT_DIR", tmp_path / "units")
    monkeypatch.setattr(
        service, "_systemctl",
        lambda *a, **k: __import__("subprocess").CompletedProcess(a, 0, "", ""))
    monkeypatch.setenv("HOIST_HOME", str(state))
