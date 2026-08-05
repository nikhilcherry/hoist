"""`hoist ls` state reporting.

An adopted app is the case worth pinning. hoist does not manage its process, so
there is no unit to ask -- but a dead one keeps its DNS record and its ingress
rule, which means the public URL answers 502 while nothing local looks wrong.
`ls` is the only place that can say so.
"""

from __future__ import annotations

import argparse

import pytest

from hoist import cli


def _state(**app):
    base = {"name": "app", "port": 8420, "hostname": "app.example.com", "tunnel": True}
    base.update(app)
    return {"apps": {base["name"]: base}, "domain": "example.com", "version": 1}


def _run_ls(monkeypatch, capsys, *, state, listening, unit_status="active"):
    monkeypatch.setattr(cli.config, "load", lambda: state)
    monkeypatch.setattr(cli.detect, "port_in_use", lambda _port: listening)
    monkeypatch.setattr(cli.service, "status", lambda _name: unit_status)
    assert cli.cmd_ls(argparse.Namespace()) == 0
    return capsys.readouterr().out


def test_an_adopted_app_whose_port_is_dead_reads_as_down(monkeypatch, capsys):
    out = _run_ls(monkeypatch, capsys, state=_state(adopted=True), listening=False)
    assert "down" in out
    # The bug this pins: the state fell through to the generic branch and
    # printed "adopted", which reads like a healthy, ordinary state.
    assert "adopted" not in out


def test_an_adopted_app_that_is_listening_reads_as_up(monkeypatch, capsys):
    out = _run_ls(monkeypatch, capsys, state=_state(adopted=True), listening=True)
    assert "up" in out
    assert "down" not in out


@pytest.mark.parametrize(
    "unit_status,listening,expected",
    [
        ("active", True, "up"),
        ("active", False, "starting"),
        ("failed", False, "failed"),
    ],
)
def test_a_managed_app_still_reports_from_its_unit(
    monkeypatch, capsys, unit_status, listening, expected
):
    out = _run_ls(
        monkeypatch, capsys, state=_state(), listening=listening, unit_status=unit_status
    )
    assert expected in out
