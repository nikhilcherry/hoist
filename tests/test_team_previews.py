"""Teammates, the audit log, and branch previews."""

from __future__ import annotations

import subprocess

import pytest

from hoist import cli, config, source
from hoist.dash import team


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "apps.json")
    monkeypatch.setattr(source, "REPO_DIR", tmp_path / "repos")
    return tmp_path


# --- team -----------------------------------------------------------------------


def test_teammate_tokens_are_stored_only_as_hashes(state):
    token = team.add("shreyas")
    raw = (state / "dash-users.json").read_text()
    assert token not in raw
    assert (state / "dash-users.json").stat().st_mode & 0o077 == 0
    assert team.identify(token, "owner-token") == "shreyas"
    assert team.identify("owner-token", "owner-token") == team.OWNER
    assert team.identify("nope", "owner-token") is None


def test_removing_a_teammate_revokes_only_their_link(state):
    a, b = team.add("a"), team.add("b")
    assert team.remove("a")
    assert team.identify(a, "o") is None and team.identify(b, "o") == "b"


@pytest.mark.parametrize("name", ["owner", "Bad Name", "", "x" * 30])
def test_bad_teammate_names_are_refused(name):
    with pytest.raises(team.TeamError):
        team.add(name)


def test_audit_log_reads_newest_first(state):
    team.audit("owner", "restart", "web")
    team.audit("shreyas", "redeploy", "web", "detail")
    rows = team.audit_tail()
    assert [r["user"] for r in rows] == ["shreyas", "owner"]


# --- branch previews ----------------------------------------------------------


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "index.html").write_text("main")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "main")
    _git(work, "push", "-q", "origin", "main")
    url = f"file://{remote}"
    source.clone(url, source.checkout_dir("web"), None)
    app = {"name": "web", "port": 1, "tunnel": False, "env": {"KEY": "v"},
           "repo": {"url": url, "branch": "main", "previews": True, "sha": "x"}}
    with config.transaction() as st:
        st["apps"]["web"] = app
    return work, app


@pytest.fixture
def hoist_calls(monkeypatch):
    """Record `hoist up/down` subprocesses and fake their effect on state."""
    calls = []
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if not (isinstance(args, list) and len(args) > 3 and args[1:3] == ["-m", "hoist"]):
            return real_run(args, **kwargs)
        calls.append(args)
        verb = args[3]
        if verb == "up":
            name = args[args.index("--name") + 1]
            branch = args[args.index("--branch") + 1]
            ok = branch != "broken"
            if ok:
                with config.transaction() as st:
                    st["apps"][name] = {"name": name, "port": 2, "tunnel": False, "preview_of": "web",
                                        "repo": {"url": "u", "branch": branch}}
            return subprocess.CompletedProcess(args, 0 if ok else 1, "", "")
        if verb == "down":
            with config.transaction() as st:
                st["apps"].pop(args[4], None)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.notify, "send", lambda *a, **k: (True, ""))
    return calls


def test_a_new_branch_gets_a_preview_with_the_apps_env(repo, hoist_calls):
    work, app = repo
    _git(work, "checkout", "-q", "-b", "dark-mode")
    _git(work, "push", "-q", "origin", "dark-mode")
    cli._reconcile_previews("web", app)
    up = hoist_calls[0]
    assert up[3] == "up" and "web-dark-mode" in up and "--local" in up and "KEY=v" in up
    assert config.load()["apps"]["web-dark-mode"]["preview_of"] == "web"


def test_a_deleted_branch_takes_its_preview_down(repo, hoist_calls):
    work, app = repo
    _git(work, "checkout", "-q", "-b", "feature")
    _git(work, "push", "-q", "origin", "feature")
    cli._reconcile_previews("web", app)
    _git(work, "push", "-q", "origin", "--delete", "feature")
    cli._reconcile_previews("web", app)
    assert any(c[3] == "down" and c[4] == "web-feature" for c in hoist_calls)
    assert "web-feature" not in config.load()["apps"]


def test_a_preview_that_failed_is_not_rebuilt_until_the_branch_moves(repo, hoist_calls):
    work, app = repo
    _git(work, "checkout", "-q", "-b", "broken")
    _git(work, "push", "-q", "origin", "broken")
    cli._reconcile_previews("web", app)
    cli._reconcile_previews("web", config.load()["apps"]["web"])
    assert sum(1 for c in hoist_calls if c[3] == "up") == 1
    (work / "index.html").write_text("fixed?")
    _git(work, "commit", "-qam", "try again")
    _git(work, "push", "-q", "origin", "broken")
    cli._reconcile_previews("web", config.load()["apps"]["web"])
    assert sum(1 for c in hoist_calls if c[3] == "up") == 2


def test_previews_stop_at_the_limit(repo, hoist_calls, monkeypatch):
    work, app = repo
    monkeypatch.setattr(cli, "PREVIEW_LIMIT", 2)
    for b in ("a", "b", "c"):
        _git(work, "checkout", "-q", "-b", b)
        _git(work, "push", "-q", "origin", b)
    for _ in range(4):
        cli._reconcile_previews("web", config.load()["apps"]["web"])
    previews = [n for n, a in config.load()["apps"].items() if a.get("preview_of") == "web"]
    assert len(previews) == 2
