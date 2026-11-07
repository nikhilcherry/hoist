"""Push-to-deploy: repo URLs, build detection, and the sync loop.

The sync tests drive real git against a local bare repo, so they exercise the
same fetch/reset path the timer runs -- only systemd is faked.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hoist import cli, config, source


# --- recognising repo URLs ----------------------------------------------------


@pytest.mark.parametrize(
    "target,expected",
    [
        ("github.com/team/app", True),
        ("https://github.com/team/app.git", True),
        ("git@github.com:team/app.git", True),
        ("file:///tmp/repo.git", True),
        (".", False),
        ("./my-app", False),
        ("app", False),
        (None, False),
    ],
)
def test_is_repo_url(target, expected):
    assert source.is_repo_url(target) is expected


def test_bare_host_path_becomes_https():
    assert source.normalize_url("github.com/team/app") == "https://github.com/team/app.git"
    assert source.normalize_url("git@github.com:team/app.git") == "git@github.com:team/app.git"


@pytest.mark.parametrize(
    "url,name",
    [
        ("https://github.com/team/My-App.git", "My-App"),
        ("git@github.com:team/app.git", "app"),
        ("file:///srv/demo/", "demo"),
    ],
)
def test_repo_name(url, name):
    assert source.repo_name(url) == name


# --- build detection ----------------------------------------------------------


def _pkg(tmp_path, scripts, lock=None):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}))
    if lock:
        (tmp_path / lock).write_text("")


def test_npm_start_with_build_script_builds_first(tmp_path):
    _pkg(tmp_path, {"start": "next start", "build": "next build"}, "package-lock.json")
    assert source.detect_build(tmp_path, "npm start") == ["npm ci", "npm run build"]


def test_dev_server_skips_the_build(tmp_path):
    _pkg(tmp_path, {"dev": "vite", "build": "vite build"})
    assert source.detect_build(tmp_path, "npm run dev") == ["npm install"]


def test_pnpm_lockfile_picks_pnpm(tmp_path):
    _pkg(tmp_path, {"start": "node ."}, "pnpm-lock.yaml")
    assert source.detect_build(tmp_path, "npm start") == ["pnpm install"]


def test_requirements_get_a_venv_and_the_command_uses_it(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    steps = source.detect_build(tmp_path, "/usr/bin/python3 app.py")
    assert steps[-1] == ".venv/bin/pip install -q -r requirements.txt"
    cmd = source.use_venv(tmp_path, "/usr/bin/python3 app.py")
    assert cmd == f"{tmp_path / '.venv' / 'bin' / 'python'} app.py"


def test_static_site_needs_no_build(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert source.detect_build(tmp_path, "python3 -m http.server $PORT") == []


def test_failed_build_reports_the_step_and_output(tmp_path):
    ok, detail = source.run_build(tmp_path, ["echo boom >&2; exit 3"], log=lambda _m: None)
    assert not ok
    assert "exited 3" in detail and "boom" in detail


# --- sync units ---------------------------------------------------------------


def test_sync_unit_name_cannot_collide_with_an_app():
    # slug() collapses "--", so no app's own unit can be named like this.
    assert "--" in source.sync_unit_base("app")
    assert "--" not in cli.detect.slug("app--sync")


def test_sync_units_run_hoist_sync_on_a_timer():
    service_text, timer_text = source.render_sync_units("demo", interval=15)
    assert f"{sys.executable} -m hoist sync demo" in service_text
    assert "Type=oneshot" in service_text
    assert "OnUnitInactiveSec=15" in timer_text


# --- the sync loop against real git -------------------------------------------


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "HOME": str(cwd), "PATH": "/usr/bin:/bin"})


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A bare 'GitHub' repo, a teammate's clone, and hoist state in tmp."""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state" / "apps.json")
    monkeypatch.setattr(source, "REPO_DIR", tmp_path / "state" / "repos")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    mate = tmp_path / "mate"
    mate.mkdir()
    _git(mate, "init", "-q", "-b", "main")
    _git(mate, "remote", "add", "origin", str(remote))
    (mate / "index.html").write_text("v1")
    _git(mate, "add", ".")
    _git(mate, "commit", "-qm", "first")
    _git(mate, "push", "-q", "origin", "main")

    restarts: list[str] = []
    monkeypatch.setattr(cli.service, "restart", lambda name: restarts.append(name))
    monkeypatch.setattr(cli, "_wait_for_healthy", lambda *_a, **_k: "up")

    url = f"file://{remote}"
    checkout = source.checkout_dir("demo")
    branch = source.clone(url, checkout, None)
    state = config.load()
    config.put_app(state, {
        "name": "demo", "port": 9999, "tunnel": False,
        "repo": {"url": url, "branch": branch, "sha": source.head(checkout), "build": []},
    })
    config.save(state)
    return {"mate": mate, "checkout": checkout, "restarts": restarts}


def _sync(force=False):
    return cli.cmd_sync(argparse.Namespace(name="demo", force=force))


def _push(mate, text, msg):
    (mate / "index.html").write_text(text)
    _git(mate, "commit", "-qam", msg)
    _git(mate, "push", "-q", "origin", "main")


def test_no_new_commit_means_no_restart(world):
    assert _sync() == 0
    assert world["restarts"] == []


def test_a_teammates_push_is_pulled_and_restarted(world):
    _push(world["mate"], "v2", "second")
    assert _sync() == 0
    assert (world["checkout"] / "index.html").read_text() == "v2"
    assert world["restarts"] == ["demo"]
    repo = config.load()["apps"]["demo"]["repo"]
    assert "second" in repo["commit"]

    _sync()  # same commit again: nothing to do
    assert world["restarts"] == ["demo"]


def test_a_broken_build_keeps_the_old_deploy_and_is_not_retried(world):
    state = config.load()
    state["apps"]["demo"]["repo"]["build"] = ["grep -q good index.html"]
    config.save(state)
    good_sha = state["apps"]["demo"]["repo"]["sha"]

    _push(world["mate"], "broken", "oops")
    _sync()
    repo = config.load()["apps"]["demo"]["repo"]
    assert repo["sha"] == good_sha  # still serving the last good commit
    assert repo["failed_sha"] != good_sha
    assert world["restarts"] == []

    _sync()  # the same broken commit is not rebuilt every tick
    assert world["restarts"] == []

    _push(world["mate"], "good now", "fix")
    _sync()
    repo = config.load()["apps"]["demo"]["repo"]
    assert "failed_sha" not in repo
    assert world["restarts"] == ["demo"]


def test_offline_fetch_is_skipped_quietly(world, monkeypatch):
    def offline(*_a, **_k):
        raise source.SourceError("git fetch failed: could not resolve host")
    monkeypatch.setattr(source, "remote_head", offline)
    assert _sync() == 0
    assert world["restarts"] == []


def test_installed_deps_survive_a_redeploy(world):
    (world["checkout"] / "node_modules").mkdir()
    (world["checkout"] / "node_modules" / "dep.js").write_text("x")
    _push(world["mate"], "v2", "second")
    _sync()
    assert (world["checkout"] / "node_modules" / "dep.js").exists()
