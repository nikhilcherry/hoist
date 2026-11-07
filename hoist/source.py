"""Git-backed apps: clone a repo, build it, and keep it in step with the remote.

`hoist up github.com/team/app` clones into a hoist-owned checkout, and a
systemd timer runs `hoist sync <name>` every few seconds. A new commit on the
tracked branch is fetched, built, and the service restarted -- so a teammate
deploys by pushing, with no access to this machine.

Polling rather than a webhook is deliberate: nothing has to accept inbound
connections, and it works on venue Wi-Fi that blocks them.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

REPO_DIR = config.STATE_DIR / "repos"
SYNC_INTERVAL = 20  # seconds between remote checks
BUILD_TIMEOUT = 15 * 60

_SCP_LIKE = re.compile(r"^[\w.-]+@[\w.-]+:[\w./-]+$")  # git@github.com:team/app.git
_HOST_PATH = re.compile(r"^(github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)/[\w.-]+/[\w.-]+/?$")


class SourceError(Exception):
    pass


# --- recognising and naming repos ---------------------------------------------


def is_repo_url(target: str | None) -> bool:
    if not target:
        return False
    if target.startswith(("https://", "http://", "ssh://", "git://", "file://")):
        return True
    return bool(_SCP_LIKE.match(target) or _HOST_PATH.match(target))


def normalize_url(target: str) -> str:
    """`github.com/team/app` -> `https://github.com/team/app.git`; others unchanged."""
    if _HOST_PATH.match(target):
        return "https://" + target.rstrip("/") + ("" if target.endswith(".git") else ".git")
    return target


def repo_name(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def checkout_dir(name: str) -> Path:
    return REPO_DIR / name


# --- git ------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None, timeout: int = 120) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never hang on a password prompt
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except FileNotFoundError as exc:
        raise SourceError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git {args[0]} timed out") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise SourceError(f"git {args[0]} failed: {message[-1] if message else 'unknown error'}")
    return result.stdout.strip()


def clone(url: str, dest: Path, branch: str | None) -> str:
    """Fresh clone (replacing any old checkout). Returns the branch it tracks."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--depth", "1"]
    if branch:
        args += ["--branch", branch]
    _git(*args, url, str(dest), timeout=600)
    # Keep install output out of `git clean`, even when the repo forgets to
    # ignore it -- otherwise every deploy would reinstall from scratch.
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as fh:
        fh.write("\n# hoist\n.venv/\nnode_modules/\n")
    return branch or _git("rev-parse", "--abbrev-ref", "HEAD", cwd=dest)


def head(dest: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=dest)


def remote_head(dest: Path, branch: str) -> str:
    _git("fetch", "--depth", "1", "origin", branch, cwd=dest, timeout=120)
    return _git("rev-parse", "FETCH_HEAD", cwd=dest)


def checkout(dest: Path, sha: str) -> None:
    """Move the hoist-owned checkout to `sha`, discarding local edits.

    Build output listed in .gitignore (node_modules, .venv, .next) survives,
    so incremental installs stay fast.
    """
    _git("reset", "--hard", sha, cwd=dest)
    _git("clean", "-fd", cwd=dest)


def subject(dest: Path, sha: str | None = None) -> str:
    try:
        return _git("log", "-1", "--format=%h %an: %s", *([sha] if sha else []), cwd=dest)
    except SourceError:
        return ""


# --- releases -------------------------------------------------------------------
#
# The checkout above is only a git cache. Every deploy is exported from it into
# its own release directory, built there, and only then made live by swapping
# the `current` symlink. The running process keeps its own working directory
# (the release it started in), so a build that fails half way -- or one that
# deletes files -- never touches what is serving. Recent releases are kept, so
# rolling back to one of them is a symlink swap and a restart, no rebuild.

KEEP_RELEASES = 4
BUILT_MARK = ".hoist-built"


def releases_dir(name: str) -> Path:
    return REPO_DIR / ".releases" / name


def current_link(name: str) -> Path:
    return releases_dir(name) / "current"


def live_release(name: str) -> Path | None:
    link = current_link(name)
    return link.resolve() if link.is_symlink() else None


def find_release(name: str, sha: str) -> Path | None:
    """A previously built release of `sha`, newest first, if one is kept."""
    root = releases_dir(name)
    if not root.is_dir():
        return None
    found = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                    and p.name.endswith(sha[:12]) and (p / BUILT_MARK).exists()),
                   key=lambda p: p.name, reverse=True)
    return found[0] if found else None


def prepare_release(name: str, cache: Path, sha: str) -> Path:
    """Export `sha` from the git cache into a fresh release directory."""
    root = releases_dir(name)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{int(time.time() * 1000)}-{sha[:12]}"
    dest.mkdir()
    archive = subprocess.run(["git", "archive", "--format=tar", sha], cwd=cache,
                             capture_output=True)
    if archive.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise SourceError(f"git archive failed: {archive.stderr.decode(errors='replace').strip()}")
    unpack = subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True)
    if unpack.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise SourceError(f"could not unpack the release: {unpack.stderr.decode(errors='replace').strip()}")
    _reuse_node_modules(name, dest)
    return dest


def _reuse_node_modules(name: str, dest: Path) -> None:
    """Hard-link the live release's node_modules when the lockfile is unchanged.

    Cheap (no bytes copied) and safe: npm replaces files rather than editing
    them in place, so the live release's copy is never modified.
    """
    live = live_release(name)
    if not live or not (live / "node_modules").is_dir():
        return
    for lock in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
        old, new = live / lock, dest / lock
        if old.is_file() and new.is_file() and old.read_bytes() == new.read_bytes():
            subprocess.run(["cp", "-al", str(live / "node_modules"), str(dest / "node_modules")],
                           capture_output=True)
            return


def mark_built(release: Path) -> None:
    (release / BUILT_MARK).write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")


def activate(name: str, release: Path) -> None:
    """Point `current` at `release` atomically."""
    link = current_link(name)
    tmp = link.with_name(f".current-{os.getpid()}")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(release.name, tmp)
    os.replace(tmp, link)


def prune_releases(name: str, keep: int = KEEP_RELEASES) -> None:
    root = releases_dir(name)
    if not root.is_dir():
        return
    live = live_release(name)
    dirs = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),
                  key=lambda p: p.name, reverse=True)
    kept = 0
    for path in dirs:
        if path == live:
            continue
        if kept < keep - 1 and (path / BUILT_MARK).exists():
            kept += 1
            continue
        shutil.rmtree(path, ignore_errors=True)


def remove_all(name: str) -> None:
    shutil.rmtree(checkout_dir(name), ignore_errors=True)
    shutil.rmtree(releases_dir(name), ignore_errors=True)


# --- building -----------------------------------------------------------------


def _npm_like(directory: Path) -> str:
    if (directory / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (directory / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def detect_build(directory: Path, start_cmd: str) -> list[str]:
    """Install/build steps a fresh checkout needs before `start_cmd` can run."""
    steps: list[str] = []
    pkg = directory / "package.json"
    if pkg.is_file():
        tool = _npm_like(directory)
        if tool == "npm":
            steps.append("npm ci" if (directory / "package-lock.json").is_file() else "npm install")
        else:
            steps.append(f"{tool} install")
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        # `start` usually serves a production build (Next.js, Vite preview...);
        # a `dev` server builds on the fly and needs no separate step.
        if "build" in scripts and re.search(r"\b(start|preview|serve)\b", start_cmd):
            steps.append(f"{tool} run build")
    if (directory / "requirements.txt").is_file():
        steps.append(f"{shlex.quote(sys.executable)} -m venv .venv")
        steps.append(".venv/bin/pip install -q -r requirements.txt")
    return steps


def use_venv(directory: Path, command: str) -> str:
    """Point a detected `python3 ...` command at the checkout's venv."""
    venv_python = directory / ".venv" / "bin" / "python"
    parts = command.split(" ", 1)
    if parts and Path(parts[0]).name.startswith("python") and (directory / "requirements.txt").is_file():
        return " ".join([str(venv_python), *parts[1:]])
    return command


def run_build(directory: Path, steps: list[str], log=print,
              transcript: list[str] | None = None) -> tuple[bool, str]:
    """Run each step in a login shell, streaming its output through `log`.

    Returns (ok, the step and the tail of its output on failure). Every line,
    commands included, is also appended to `transcript` when one is given --
    that is what a deploy's history entry keeps.
    """
    record = transcript if transcript is not None else []

    def say(line: str) -> None:
        record.append(line)
        log(line)

    for step in steps:
        say(f"$ {step}")
        proc = subprocess.Popen(
            ["/bin/sh", "-lc", step], cwd=directory, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace",
        )
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(BUILD_TIMEOUT, kill)
        timer.start()
        lines: list[str] = []
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                say(line)
            proc.wait()
        finally:
            timer.cancel()
        if timed_out.is_set():
            return False, f"{step}: timed out after {BUILD_TIMEOUT // 60} min"
        if proc.returncode != 0:
            tail = "\n".join(lines[-12:])
            return False, f"{step} exited {proc.returncode}\n{tail}"
    return True, ""


def fetch_sha(dest: Path, sha: str) -> str:
    """Make one specific commit available in the shallow checkout (rollback)."""
    _git("fetch", "--depth", "1", "origin", sha, cwd=dest, timeout=120)
    return _git("rev-parse", "FETCH_HEAD", cwd=dest)


HISTORY_LIMIT = 30


def record_deploy(repo: dict, entry: dict) -> None:
    """Prepend a deploy to the app's history, keeping the newest few."""
    history = repo.setdefault("history", [])
    history.insert(0, entry)
    del history[HISTORY_LIMIT:]


# --- the sync timer -------------------------------------------------------------


def sync_unit_base(name: str) -> str:
    # `--` can never appear in an app name (slug collapses it), so this cannot
    # collide with another app's service unit.
    return f"hoist-{name}--sync"


def render_sync_units(name: str, interval: int = SYNC_INTERVAL) -> tuple[str, str]:
    pkg_root = Path(__file__).resolve().parent.parent
    env = f"Environment=PYTHONPATH={pkg_root}\n"
    if os.environ.get("HOIST_HOME"):
        env += f"Environment=HOIST_HOME={os.environ['HOIST_HOME']}\n"
    service = f"""[Unit]
Description=hoist: pull and redeploy {name} when its branch moves

[Service]
Type=oneshot
{env}ExecStart={shlex.quote(sys.executable)} -m hoist sync {name}
StandardOutput=journal
StandardError=journal
"""
    timer = f"""[Unit]
Description=hoist: check {name} for new commits every {interval}s

[Timer]
OnActiveSec=5
OnUnitInactiveSec={interval}
AccuracySec=2

[Install]
WantedBy=timers.target
"""
    return service, timer


def install_sync_timer(name: str, unit_dir: Path, systemctl) -> None:
    unit_dir.mkdir(parents=True, exist_ok=True)
    base = sync_unit_base(name)
    service_text, timer_text = render_sync_units(name)
    (unit_dir / f"{base}.service").write_text(service_text)
    (unit_dir / f"{base}.timer").write_text(timer_text)
    systemctl("daemon-reload")
    result = systemctl("enable", "--now", f"{base}.timer")
    if result.returncode != 0:
        raise SourceError(result.stderr.strip() or "could not enable the sync timer")


def remove_sync_timer(name: str, unit_dir: Path, systemctl) -> None:
    base = sync_unit_base(name)
    systemctl("disable", "--now", f"{base}.timer")
    for suffix in (".timer", ".service"):
        (unit_dir / f"{base}{suffix}").unlink(missing_ok=True)
    systemctl("daemon-reload")
