"""Keep every test away from the real systemd user directory."""

import pytest

from hoist import service


@pytest.fixture(autouse=True)
def _isolated_units(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "UNIT_DIR", tmp_path / "units")
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: __import__("subprocess").CompletedProcess(a, 0, "", ""))
