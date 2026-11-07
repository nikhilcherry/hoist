"""hoist -- put a local app on a public HTTPS URL, with one command."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import shlex
import shutil
import subprocess

from . import config, containers, detect, notify, qr, service, source, tunnel, ui

__version__ = "0.1.0"

HEALTH_TIMEOUT = 20.0
CRASH_LOOP_RESTARTS = 2


class UserError(Exception):
    """Something the user can fix; printed without a traceback."""


# --- helpers ------------------------------------------------------------------


def _wait_for_healthy(name: str, port: int, timeout: float = HEALTH_TIMEOUT, check=None) -> str:
    """Wait for the port to open. Returns "up", "failed" or "timeout".

    The unit state is watched alongside the port so a crashed app is reported
    in a second or two instead of after the full timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (check or detect.port_in_use)(port):
            return "up"
        if service.status(name) == "failed":
            return "failed"
        if service.n_restarts(name) >= CRASH_LOOP_RESTARTS:
            return "crashing"
        time.sleep(0.25)
    return "timeout"


def _report_health(name: str, port: int, timeout: float = HEALTH_TIMEOUT, check=None) -> str:
    health = _wait_for_healthy(name, port, timeout, check)
    if health == "up":
        ui.ok(f"listening on 127.0.0.1:{port}")
        return health
    if health == "crashing":
        ui.fail(f"{name} keeps crashing on startup")
    elif health == "failed":
        ui.fail(f"{name} failed to start")
    else:
        ui.warn(f"nothing listening on port {port} after {int(timeout)}s")
    tail = service.recent_logs(name)
    for line in tail.splitlines()[-8:]:
        ui.step(line)
    ui.step(f"full logs: hoist logs {name}")
    return health


def _parse_env(pairs: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise UserError(f"--env expects KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        env[key.strip()] = value
    return env


def _resolve_domain(state: dict, explicit: str | None, cf_text: str | None) -> str:
    if explicit:
        return explicit.lstrip(".")
    if state.get("domain"):
        return str(state["domain"])
    if cf_text:
        guess = tunnel.default_domain(cf_text)
        if guess:
            return guess
    raise UserError(
        "could not work out which domain to use -- pass --domain example.com "
        "(it is remembered for next time)"
    )


def _show_qr(url: str, ascii_mode: bool = False) -> None:
    try:
        print(qr.render_ascii(url) if ascii_mode else qr.render(url))
    except ValueError as exc:
        ui.warn(f"no QR code: {exc}")


def _app_url(app: dict) -> str:
    if app.get("kind") == "db":
        return app["db"]["url"]
    if app.get("tunnel"):
        return f"https://{app['hostname']}"
    return f"http://{app.get('lan_ip', '127.0.0.1')}:{app['port']}"


def _pick_port(args: argparse.Namespace, existing: dict | None) -> int:
    """Reuse our own port across restarts; never steal someone else's."""
    wanted = args.port or (existing or {}).get("port")
    if wanted is None:
        return detect.free_port()
    if detect.port_is_free(wanted):
        return int(wanted)
    if existing and existing.get("port") == wanted:
        return int(wanted)  # it is our own service still holding it
    raise UserError(f"port {wanted} is already in use")


def _apply_ingress(
    cf_path: Path, cf_text: str, name: str, hostname: str, port: int
) -> str:
    """Write, validate and roll back the ingress edit. Returns the new text."""
    try:
        updated = tunnel.upsert(cf_text, name, hostname, port)
    except tunnel.TunnelError as exc:
        raise UserError(str(exc)) from exc
    if updated == cf_text:
        return updated
    if tunnel.needs_sudo(cf_path):
        ui.step(f"sudo needed to edit {cf_path}")
    backup = tunnel.write_config(cf_path, updated)
    good, message = tunnel.validate(cf_path)
    if not good:
        tunnel.restore(cf_path, backup)
        raise UserError(f"ingress config rejected, rolled back: {message}")
    ui.ok(f"ingress rule added {ui.dim(str(cf_path))}")
    return updated


def _local_body(port: int, limit: int = 256) -> bytes:
    """Grab a sample of what the app serves, to compare against the public URL."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            return response.read(limit)
    except Exception:
        return b""


def _verify_public(url: str, port: int, attempts: int = 5) -> None:
    """Confirm the public URL really reaches this app, not something else.

    On a zone with a wildcard DNS record any hostname resolves and returns a
    page, so a half-finished publish looks like it worked. Comparing against
    the local response is what actually catches that.
    """
    expected = _local_body(port)
    for attempt in range(attempts):
        good, detail = tunnel.reaches_tunnel(url, expected)
        if good:
            ui.ok(f"public URL verified ({detail})")
            return
        if attempt < attempts - 1:
            time.sleep(2.0)
    ui.warn(f"{url} did not serve this app: {detail}")
    ui.step("DNS may still be propagating — re-check in a minute")
    ui.step("if a wildcard record covers this zone, it can shadow the hostname")


def _publish(text: str, hostname: str) -> None:
    tid = tunnel.tunnel_id(text)
    if tid:
        routed, message = tunnel.route_dns(tid, hostname)
        if routed:
            ui.ok(f"DNS → {hostname}")
        else:
            ui.warn(f"DNS not set automatically: {message}")
    else:
        ui.warn("no `tunnel:` id in config, skipped DNS")
    reloaded, detail = tunnel.reload()
    if reloaded:
        ui.ok("cloudflared reloaded")
    else:
        ui.warn(f"could not reload cloudflared: {detail}")


# --- commands -----------------------------------------------------------------


def cmd_up(args: argparse.Namespace) -> int:
    state = config.load()
    repo: dict | None = None

    if source.is_repo_url(args.target):
        url = source.normalize_url(args.target)
        name = detect.slug(args.name or source.repo_name(url))
        cache = source.checkout_dir(name)
        ui.step(f"cloning {url}")
        try:
            branch = source.clone(url, cache, getattr(args, "branch", None))
            sha = source.head(cache)
            target = source.prepare_release(name, cache, sha)
        except source.SourceError as exc:
            raise UserError(str(exc)) from exc
        repo = {"url": url, "branch": branch}
        ui.ok(f"cloned {branch} @ {source.subject(cache)}")
    else:
        target = Path(args.target).expanduser().resolve() if args.target else Path.cwd()
        if not target.is_dir():
            raise UserError(f"not a directory: {target}")
        name = detect.slug(args.name or target.name)

    if args.cmd:
        command, how = args.cmd, "custom command"
    else:
        command, how = detect.detect_command(target)

    kind = None
    compose_path = None
    generated = False
    if command == detect.DOCKERFILE:
        if not containers.available():
            raise UserError("this project has a Dockerfile, and hoist needs a running Docker for it")
        compose_path = containers.ensure_dockerfile_compose(target)
        generated = True
        how = f"Dockerfile (port {containers.dockerfile_port(target)} inside)"
        command = detect.COMPOSE
    if command == detect.COMPOSE:
        if not containers.available():
            raise UserError("this project is a Compose stack, and hoist needs a running Docker for it")
        compose_path = compose_path or containers.compose_file(target)
        kind = "compose"
        if not generated:
            how = f"Compose stack ({compose_path.name})"
        command = containers.compose_up_command(name, generated) + ("" if repo is not None else " --build")
        if not args.port and not containers.compose_uses_port_var(compose_path):
            published = containers.compose_published_port(compose_path)
            if published is None:
                raise UserError("the compose file publishes no port — add `ports: [\"${PORT}:<container port>\"]`"
                                " to the web service, or pass --port")
            args.port = published

    if repo is not None:
        build = getattr(args, "build", None)
        if kind == "compose" and not build:
            steps = [containers.compose_build_step(name, generated)]
        else:
            steps = [build] if build else source.detect_build(target, command)
        ok, detail = source.run_build(target, steps, log=ui.step)
        if not ok:
            shutil.rmtree(target, ignore_errors=True)
            raise UserError(f"build failed: {detail}")
        if steps:
            ui.ok("built")
        source.mark_built(target)
        source.activate(name, target)
        source.prune_releases(name)
        target = source.current_link(name)
        if not args.cmd:
            command = source.use_venv(target, command)
        repo.update(sha=sha, build=steps, deployed=config.now(),
                    commit=source.subject(cache, sha))
        previous = config.get_app(state, name)
        if previous and (previous.get("repo") or {}).get("history"):
            repo["history"] = previous["repo"]["history"]
        source.record_deploy(repo, {
            "sha": repo["sha"], "commit": repo["commit"], "at": config.now(),
            "trigger": "initial", "status": "ok", "seconds": 0, "log": [],
        })

    existing = config.get_app(state, name)
    port = _pick_port(args, existing)

    use_tunnel = not args.local
    cf_path: Path | None = None
    cf_text: str | None = None
    domain: str | None = None
    hostname: str | None = None

    if use_tunnel:
        if not tunnel.have_cloudflared():
            raise UserError(
                "cloudflared is not installed -- install it, or use --local "
                "for a LAN-only URL"
            )
        cf_path = tunnel.find_config()
        cf_text = cf_path.read_text()
        domain = _resolve_domain(state, args.domain, cf_text)
        hostname = args.hostname or f"{name}.{domain}"

    env = _parse_env(args.env)
    if args.local:
        env.setdefault("HOST", "0.0.0.0")
        command = command.replace("--bind 127.0.0.1", "--bind 0.0.0.0")

    app = {
        "name": name,
        "port": port,
        "workdir": str(target),
        "cmd": command,
        "env": env,
        "tunnel": use_tunnel,
        "hostname": hostname,
        "created": (existing or {}).get("created") or config.now(),
        "updated": config.now(),
    }
    if repo is not None:
        app["repo"] = repo
    if kind == "compose":
        app["kind"] = "compose"
    if generated:
        app["dockerfile"] = True
    if getattr(args, "preview_of", None):
        app["preview_of"] = args.preview_of

    ui.info(f"{ui.bold(name)} {ui.dim('·')} {how} {ui.dim('·')} port {port}")
    ui.step(f"$ {command.replace('$PORT', str(port))}")

    if not service.have_systemd():
        raise UserError("systemd --user is unavailable; hoist needs it to run apps")

    service.write_unit(app)
    try:
        if existing:
            service.restart(name)
        else:
            service.start(name)
    except service.ServiceError as exc:
        raise UserError(f"failed to start service: {exc}") from exc

    health = _report_health(name, port, timeout=240 if kind == "compose" else HEALTH_TIMEOUT,
                            check=detect.http_answers if kind == "compose" else None)

    # The app is running from here on, so record it before anything that can
    # fail -- otherwise a failed tunnel step leaves a live service that
    # `hoist ls` cannot see and `hoist down` cannot stop.
    config.put_app(state, app)
    config.save(state)

    if use_tunnel:
        assert cf_path is not None and cf_text is not None and hostname is not None
        updated = _apply_ingress(cf_path, cf_text, name, hostname, port)
        _publish(updated, hostname)
        state["domain"] = domain
        config.save(state)
        if health == "up":
            _verify_public(f"https://{hostname}", port)
    else:
        app["lan_ip"] = detect.lan_ip()
        config.put_app(state, app)
        config.save(state)

    if repo is not None and not getattr(args, "no_sync", False):
        try:
            source.install_sync_timer(name, service.UNIT_DIR, service.systemctl)
            ui.ok(f"watching {repo['branch']} — every push redeploys (checks every {source.SYNC_INTERVAL}s)")
        except source.SourceError as exc:
            ui.warn(f"auto-deploy not enabled: {exc}")

    url = _app_url(app)
    ui.banner(url, f"hoist logs {name}   ·   hoist down {name}")
    if not args.no_qr and health == "up":
        _show_qr(url, args.ascii)
    return 0 if health == "up" else 1


def cmd_share(args: argparse.Namespace) -> int:
    """Serve a folder of files publicly, no project scaffolding required."""
    directory = Path(args.path).expanduser().resolve()
    if not directory.is_dir():
        raise UserError(f"not a directory: {directory}")
    python = sys.executable or "python3"
    bind = "0.0.0.0" if args.local else "127.0.0.1"
    args.target = str(directory)
    args.cmd = f"{python} -m http.server $PORT --bind {bind} --directory {directory}"
    args.name = args.name or directory.name
    return cmd_up(args)


def cmd_ls(args: argparse.Namespace) -> int:
    state = config.load()
    apps = state["apps"]
    if not apps:
        ui.info("nothing hoisted yet — try: hoist up .")
        return 0

    rows = []
    for name in sorted(apps):
        app = apps[name]
        unit_state = "adopted" if app.get("adopted") else service.status(name)
        if app.get("stopped"):
            unit_state = "stopped"
        listening = detect.port_in_use(app["port"])
        if listening and unit_state in ("active", "adopted"):
            health = ui.green("up")
        elif unit_state == "adopted":
            # There is no unit to ask about an adopted process, so the port is
            # the only evidence there is -- and a dead one still has a DNS
            # record and an ingress rule pointing at it, so the public URL
            # answers 502. Printing "adopted" here read like an ordinary state
            # and hid exactly that.
            health = ui.red("down")
        elif unit_state == "active":
            health = ui.yellow("starting")
        elif unit_state == "failed":
            health = ui.red("failed")
        else:
            health = ui.dim(unit_state)
        repo = app.get("repo")
        src = (f"{repo['branch']}@{repo.get('sha', '')[:7]}" if repo
               else app["db"]["engine"] if app.get("kind") == "db" else ui.dim("local"))
        if repo and repo.get("paused"):
            src += " " + ui.yellow("(paused)")
        if repo and repo.get("failed_sha"):
            src += " " + ui.red(f"(build of {repo['failed_sha'][:7]} failed)")
        shown = _app_url(app)
        if app.get("kind") == "db":  # never print a password in a listing
            shown = re.sub(r"(://[^:/@]*:)[^@]+@", r"\1•••@", shown)
        rows.append([name, str(app["port"]), health, src, shown])
    ui.table(rows, ["NAME", "PORT", "STATE", "SOURCE", "URL"])
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")

    if app.get("repo"):
        source.remove_sync_timer(args.name, service.UNIT_DIR, service.systemctl)
        source.remove_all(args.name)
        ui.ok("stopped watching the repo, checkout removed")

    if not app.get("adopted"):
        service.stop_and_remove(args.name)
        ui.ok("service stopped and removed")
    if app.get("kind") == "compose":
        kept = containers.compose_down(args.name, purge=getattr(args, "purge", False))
        ui.ok("compose stack removed" + (" with its volumes" if getattr(args, "purge", False) else ""))
        if kept:
            ui.step(f"data kept in {', '.join(kept)} — `hoist down {args.name} --purge` deletes it")
    if app.get("kind") == "db":
        containers.run("rm", "-f", containers.container_name(args.name), timeout=60)
        base = containers.backup_unit_base(args.name)
        service.systemctl("disable", "--now", f"{base}.timer")
        for suffix in (".timer", ".service"):
            (service.UNIT_DIR / f"{base}{suffix}").unlink(missing_ok=True)
        if getattr(args, "purge", False):
            containers.remove_volume(args.name)
            ui.ok(f"data volume {app['db']['volume']} deleted")
        else:
            ui.step(f"data kept in volume {app['db']['volume']} — `hoist down {args.name} --purge` deletes it")

    if app.get("tunnel"):
        try:
            cf_path = tunnel.find_config()
            updated, changed = tunnel.remove(cf_path.read_text(), args.name)
            if changed:
                if tunnel.needs_sudo(cf_path):
                    ui.step(f"sudo needed to edit {cf_path}")
                backup = tunnel.write_config(cf_path, updated)
                good, message = tunnel.validate(cf_path)
                if good:
                    ui.ok("ingress rule removed")
                    reloaded, detail = tunnel.reload()
                    if not reloaded:
                        ui.warn(f"could not reload cloudflared: {detail}")
                else:
                    tunnel.restore(cf_path, backup)
                    ui.warn(f"ingress edit rolled back: {message}")
        except tunnel.TunnelError as exc:
            ui.warn(f"left ingress alone: {exc}")
        if app.get("hostname"):
            ui.step(f"DNS record for {app['hostname']} was left in place")

    config.drop_app(state, args.name)
    config.save(state)
    ui.ok(f"{args.name} is down")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Redeploy a git-backed app if its branch has moved. Run by the timer.

    A failed fetch or build never takes the app down: the running process
    keeps serving the last good commit, and a failed commit is not retried
    until something newer is pushed. `--to <sha>` rolls back to an earlier
    commit and pauses auto-deploy, so the next tick does not undo it.
    """
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None or not app.get("repo"):
        raise UserError(f"{args.name!r} is not a git-backed app")
    repo = app["repo"]
    target = source.checkout_dir(args.name)
    to = getattr(args, "to", None)
    manual = bool(args.force or to)

    if not manual and (repo.get("paused") or app.get("stopped")):
        return 0

    # One deploy per app at a time: the timer and a dashboard redeploy can
    # otherwise build the same app concurrently.
    lock_path = source.releases_dir(args.name)
    lock_path.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path / ".deploy-lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if manual else fcntl.LOCK_NB))
    except BlockingIOError:
        return 0  # a deploy is already running; the next tick re-checks

    try:
        wanted = source.fetch_sha(target, to) if to else source.remote_head(target, repo["branch"])
    except source.SourceError as exc:
        if manual:
            raise UserError(str(exc)) from exc
        print(f"fetch skipped: {exc}")
        return 0  # offline or rate-limited; the next tick tries again
    if not manual and repo.get("previews") and not app.get("preview_of"):
        _reconcile_previews(args.name, app)
    if wanted in (repo.get("sha"), repo.get("failed_sha")) and not manual:
        return 0

    started = time.monotonic()
    commit = source.subject(target, wanted)
    trigger = "rollback" if to else ("manual" if args.force else "push")
    print(f"deploying {commit} ({trigger})")
    transcript: list[str] = []
    release = source.find_release(args.name, wanted) if to else None
    if release is not None:
        ok, detail = True, ""
        transcript.append(f"reusing the release built earlier: {release.name}")
        print(transcript[-1])
    else:
        try:
            release = source.prepare_release(args.name, target, wanted)
        except source.SourceError as exc:
            if manual:
                raise UserError(str(exc)) from exc
            print(f"skipped: {exc}")
            return 0
        if app.get("dockerfile"):
            containers.ensure_dockerfile_compose(release)
        ok, detail = source.run_build(release, repo.get("build", []), transcript=transcript)

    entry = {"sha": wanted, "commit": commit, "at": config.now(), "trigger": trigger,
             "seconds": round(time.monotonic() - started, 1)}
    if os.environ.get("HOIST_ACTOR"):
        entry["by"] = os.environ["HOIST_ACTOR"]
    if not ok:
        shutil.rmtree(release, ignore_errors=True)  # never live, never needed
        entry.update(status="failed", log=transcript[-60:], error=detail.splitlines()[0])
        with config.transaction() as state:  # re-read: the build may have run for minutes
            app = config.get_app(state, args.name)
            if app is None:
                return 0  # taken down while we were building
            app["repo"]["failed_sha"] = wanted
            source.record_deploy(app["repo"], entry)
        print(f"build failed, still serving {repo.get('sha', '')[:7]}: {detail}")
        notify.send(f"{args.name}: push {wanted[:7]} failed to build",
                    f"{commit}\n{detail.splitlines()[0]}\nStill serving {repo.get('sha', '')[:7]}.",
                    level="bad", app=args.name)
        return 1 if manual else 0

    with config.transaction() as state:
        app = config.get_app(state, args.name)
        if app is None:
            shutil.rmtree(release, ignore_errors=True)
            return 0
        source.mark_built(release)
        source.activate(args.name, release)
        source.prune_releases(args.name)
        link = str(source.current_link(args.name))
        if app.get("workdir") != link:
            # An app deployed before releases existed ran straight from the git
            # cache; point its unit at the release link from now on.
            app["workdir"] = link
            app["cmd"] = app.get("cmd", "").replace(str(target), link)
            service.write_unit(app)
        app["repo"].update(sha=wanted, commit=commit, deployed=config.now())
        app["repo"].pop("failed_sha", None)
        if to:
            app["repo"]["paused"] = True
        stopped, port, kind = bool(app.get("stopped")), int(app["port"]), app.get("kind")
        hostname = app.get("hostname") if app.get("tunnel") else None

    # Restart and wait outside the lock: health checks take seconds.
    if not stopped:
        service.restart(args.name)
        health = _wait_for_healthy(args.name, port, 240 if kind == "compose" else HEALTH_TIMEOUT,
                                   detect.http_answers if kind == "compose" else None)
    else:
        health = "stopped"
    entry.update(status="ok" if health in ("up", "stopped") else "unhealthy",
                 seconds=round(time.monotonic() - started, 1), log=transcript[-60:])
    with config.transaction() as state:
        app = config.get_app(state, args.name)
        if app is not None:
            source.record_deploy(app["repo"], entry)
    print(f"deployed {commit} ({health})")
    notify.send(f"{args.name} deployed {wanted[:7]}" + (" (rollback)" if to else ""),
                f"{commit}\n{'https://' + hostname if hostname else ''}".strip(),
                level="ok" if entry["status"] == "ok" else "warn", app=args.name)
    if to:
        print("auto-deploy paused after rollback — resume with: hoist resume " + args.name)
    return 0


PREVIEW_LIMIT = 5


def preview_name(app: str, branch: str) -> str:
    return detect.slug(f"{app}-{branch}")[:40].strip("-")


def _reconcile_previews(name: str, app: dict) -> None:
    """One preview app per branch: create it on a new branch, remove it when the
    branch is deleted. At most one new preview per tick, so a burst of branches
    never stalls the main app's deploys."""
    repo = app["repo"]
    try:
        heads = source.remote_branches(source.checkout_dir(name))
    except source.SourceError as exc:
        print(f"previews skipped: {exc}")
        return
    state = config.load()
    existing = {a["repo"]["branch"]: n for n, a in state["apps"].items()
                if a.get("preview_of") == name and a.get("repo")}
    hoist = [sys.executable, "-m", "hoist"]
    for branch, pname in existing.items():
        if branch not in heads:
            print(f"branch {branch} is gone, removing preview {pname}")
            subprocess.run([*hoist, "down", pname], capture_output=True, text=True, timeout=300)
            notify.send(f"Preview {pname} removed", f"Branch {branch} was deleted.", level="info", app=pname)
    failed = dict(repo.get("preview_failed") or {})
    for branch in sorted(heads):
        if branch == repo["branch"] or branch in existing:
            continue
        if failed.get(branch) == heads[branch]:
            continue  # this commit already failed; wait for a new push to the branch
        if len(existing) >= PREVIEW_LIMIT:
            print(f"preview limit ({PREVIEW_LIMIT}) reached; {branch} gets no preview")
            break
        pname = preview_name(name, branch)
        if config.get_app(state, pname):
            continue
        args = [*hoist, "up", repo["url"], "--branch", branch, "--name", pname, "--no-qr",
                "--preview-of", name]
        if not app.get("tunnel"):
            args.append("--local")
        for key, value in (app.get("env") or {}).items():
            args += ["--env", f"{key}={value}"]
        print(f"new branch {branch}: deploying preview {pname}")
        result = subprocess.run(args, capture_output=True, text=True, timeout=1200)
        print((result.stdout + result.stderr).strip()[-2000:])
        with config.transaction() as fresh_state:
            main = config.get_app(fresh_state, name)
            if main and main.get("repo"):
                record = main["repo"].setdefault("preview_failed", {})
                if result.returncode == 0:
                    record.pop(branch, None)
                else:
                    record[branch] = heads[branch]
                for gone in [b for b in record if b not in heads]:
                    record.pop(gone)
        if result.returncode == 0:
            fresh = config.get_app(config.load(), pname) or {}
            url = _app_url(fresh) if fresh else ""
            notify.send(f"Preview of {branch} is live", f"{pname}: {url}", level="ok", app=pname)
        else:
            notify.send(f"Preview of {branch} failed to build", f"{pname} — fix the branch and push again.",
                        level="bad", app=name)
        break  # one new preview per tick


def cmd_previews(args: argparse.Namespace) -> int:
    with config.transaction() as state:
        app = config.get_app(state, args.name)
        if app is None or not app.get("repo"):
            raise UserError(f"{args.name!r} is not deployed from git")
        if app.get("preview_of"):
            raise UserError(f"{args.name} is itself a preview")
        if args.state == "on":
            app["repo"]["previews"] = True
        else:
            app["repo"].pop("previews", None)
    if args.state == "on":
        ui.ok(f"every branch pushed to {app['repo']['url']} gets its own preview app "
              f"(up to {PREVIEW_LIMIT}), removed when the branch is deleted")
    else:
        ui.ok("branch previews off — existing previews stay until you take them down")
    return 0


def _set_flag(name: str, key: str, value: bool, *, repo: bool = False) -> dict:
    with config.transaction() as state:
        app = config.get_app(state, name)
        if app is None:
            raise UserError(f"no hoisted app called {name!r}")
        holder = app.get("repo") if repo else app
        if repo and holder is None:
            raise UserError(f"{name} is not deployed from git")
        if value:
            holder[key] = True
        else:
            holder.pop(key, None)
    return app


def cmd_pause(args: argparse.Namespace) -> int:
    _set_flag(args.name, "paused", True, repo=True)
    ui.ok(f"auto-deploy paused for {args.name} — pushes are ignored until: hoist resume {args.name}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    _set_flag(args.name, "paused", False, repo=True)
    ui.ok(f"auto-deploy resumed for {args.name}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    app = _set_flag(args.name, "stopped", True)
    if app.get("adopted"):
        raise UserError(f"{args.name} was adopted; hoist does not manage its process")
    service.systemctl("stop", service.unit_name(args.name))
    ui.ok(f"{args.name} stopped (its URL now answers 502) — start it with: hoist start {args.name}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    app = _set_flag(args.name, "stopped", False)
    if app.get("adopted"):
        raise UserError(f"{args.name} was adopted; hoist does not manage its process")
    service.start(args.name)
    _report_health(args.name, app["port"])
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")
    if app.get("adopted"):
        raise UserError(f"{args.name} was adopted; hoist does not manage its logs")
    if getattr(args, "deploys", False):
        if not app.get("repo"):
            raise UserError(f"{args.name} is not deployed from git")
        return service.logs(args.name, lines=args.lines, follow=args.follow,
                            unit=f"{source.sync_unit_base(args.name)}.service")
    return service.logs(args.name, lines=args.lines, follow=args.follow)


def cmd_restart(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")
    if app.get("adopted"):
        raise UserError(f"{args.name} was adopted; hoist does not manage its process")
    service.restart(args.name)
    ui.ok(f"{args.name} restarted")
    return 0


def cmd_url(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")
    print(_app_url(app))
    return 0


def cmd_qr(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")
    url = _app_url(app)
    ui.banner(url)
    _show_qr(url, args.ascii)
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    """Expose something already running on a port, without managing its process."""
    state = config.load()
    name = detect.slug(args.name)
    if not detect.port_in_use(args.port):
        ui.warn(f"nothing is listening on port {args.port} yet")

    cf_path = tunnel.find_config()
    cf_text = cf_path.read_text()
    domain = _resolve_domain(state, args.domain, cf_text)
    hostname = args.hostname or f"{name}.{domain}"

    updated = _apply_ingress(cf_path, cf_text, name, hostname, args.port)
    _publish(updated, hostname)

    app = {
        "name": name,
        "port": args.port,
        "workdir": str(Path.cwd()),
        "cmd": "",
        "env": {},
        "tunnel": True,
        "hostname": hostname,
        "adopted": True,
        "created": config.now(),
        "updated": config.now(),
    }
    config.put_app(state, app)
    state["domain"] = domain
    config.save(state)

    url = _app_url(app)
    ui.banner(url, "adopted — hoist does not manage this process")
    if not args.no_qr:
        _show_qr(url, args.ascii)
    return 0


def doctor_checks() -> list[dict]:
    """Every setup check as data: {level: ok|warn|fail, text, fix: [commands]}.

    `hoist doctor` prints these; the dashboard's System page renders them.
    """
    checks: list[dict] = []

    def add(level: str, text: str, fix: list[str] | None = None) -> None:
        checks.append({"level": level, "text": text, "fix": fix or []})

    if service.have_systemd():
        add("ok", "systemd --user available")
    else:
        add("fail", "systemd --user unavailable — hoist cannot manage services")

    if service.lingering_enabled():
        add("ok", "lingering enabled (services survive logout)")
    else:
        add("warn", "lingering off — services stop when you log out",
            [f"sudo loginctl enable-linger {os.environ.get('USER', '$USER')}"])

    if tunnel.have_cloudflared():
        add("ok", "cloudflared installed")
    else:
        add("warn", "cloudflared not installed — only --local URLs will work")

    try:
        cf_path = tunnel.find_config()
        text = cf_path.read_text()
        add("ok", f"tunnel config: {cf_path}")
        tid = tunnel.tunnel_id(text)
        if tid:
            add("ok", f"tunnel id: {tid}")
        else:
            add("warn", "no `tunnel:` id in config")
        hosts = tunnel.hostnames(text)
        add("ok", f"{len(hosts)} ingress hostname(s): {', '.join(hosts) or '—'}")
        domain = tunnel.default_domain(text)
        if domain:
            add("ok", f"default domain: {domain}")
        else:
            add("warn", "no domain inferred — pass --domain once")
        if tunnel.needs_sudo(cf_path) and not tunnel.sudo_is_passwordless():
            user = os.environ.get("USER") or Path.home().name
            add("warn", f"{cf_path} needs root, and sudo will prompt for a password — "
                "publishing a new URL must then run in a real terminal",
                tunnel.passwordless_setup(cf_path, user))
        elif tunnel.needs_sudo(cf_path):
            add("ok", "sudo available without a password prompt")
        else:
            add("ok", "tunnel config is writable without sudo")
    except tunnel.TunnelError as exc:
        add("warn", str(exc))

    running = tunnel.daemon_unit()
    if running:
        _unit, is_system = running
        add("ok", f"cloudflared running as {'system' if is_system else 'user'} unit")
    else:
        add("warn", "cloudflared is not running as a service")

    state = config.load()
    add("ok", f"{len(state['apps'])} app(s) tracked in {config.STATE_FILE}")
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    problems = 0
    for check in doctor_checks():
        {"ok": ui.ok, "warn": ui.warn, "fail": ui.fail}[check["level"]](check["text"])
        if check["fix"]:
            if len(check["fix"]) > 1:
                ui.step("to fix it, run these once:")
            for line in check["fix"]:
                ui.step(f"    {line}" if len(check["fix"]) > 1 else f"fix: {line}")
        problems += check["level"] == "fail"
    return 1 if problems else 0


def cmd_db(args: argparse.Namespace) -> int:
    """A database in one command: Docker, a private volume, a generated password."""
    try:
        containers.docker()
    except containers.ContainerError as exc:
        raise UserError(str(exc)) from exc
    name = detect.slug(args.name or args.engine)
    state = config.load()
    if config.get_app(state, name):
        raise UserError(f"{name} already exists — pick another --name")
    port = args.port or detect.free_port()
    if not detect.port_is_free(port):
        raise UserError(f"port {port} is already in use")
    db = containers.database_spec(args.engine, name, port, args.image)
    meta = containers.ENGINES[args.engine]
    ui.info(f"{ui.bold(name)} {ui.dim('·')} {meta['label']} {ui.dim('·')} 127.0.0.1:{port}")
    try:
        containers.pull(db["image"], log=ui.step)
    except containers.ContainerError as exc:
        raise UserError(str(exc)) from exc
    app = {
        "name": name, "port": port, "workdir": str(Path.home()),
        "cmd": containers.database_command(name, db), "env": {}, "tunnel": False,
        "hostname": None, "kind": "db", "db": db, "pre": containers.pre_start(name),
        "created": config.now(), "updated": config.now(),
    }
    service.write_unit(app)
    service.start(name)
    with config.transaction() as state:
        config.put_app(state, app)
    health = _report_health(name, port, timeout=180, check=lambda _p: containers.ready(name, db))
    ui.banner(db["url"], f"hoist link {name} <app>   ·   hoist down {name}")
    ui.step("reachable from this machine only; data lives in the volume " + db["volume"])
    return 0 if health == "up" else 1


def cmd_exec(args: argparse.Namespace) -> int:
    """Run one command in an app's own environment: its folder and env for a
    process, its container for a database, its first service for a stack."""
    app = config.get_app(config.load(), args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")
    if app.get("adopted"):
        raise UserError(f"{args.name} was adopted; hoist does not know where it runs")
    command = " ".join(args.command)
    if not command.strip():
        raise UserError("nothing to run")
    if app.get("kind") == "db":
        argv = [*shlex.split(containers.docker()), "exec", containers.container_name(args.name),
                "sh", "-c", command]
        env, cwd = None, None
    elif app.get("kind") == "compose":
        base = [*shlex.split(containers.docker()), "compose", "-p", containers.compose_project(args.name)]
        services = subprocess.run([*base, "ps", "--services"], capture_output=True, text=True,
                                  cwd=app["workdir"]).stdout.split()
        if not services:
            raise UserError(f"{args.name} has no running containers")
        target = args.service or next((s for s in ("web", "app", "api", "server") if s in services), services[0])
        argv = [*base, "exec", "-T", target, "sh", "-c", command]
        env, cwd = None, app["workdir"]
    else:
        argv = ["/bin/sh", "-lc", command]
        env = dict(os.environ, PORT=str(app["port"]), HOIST_APP=args.name, **(app.get("env") or {}))
        cwd = app["workdir"]
    print(f"$ {command}", flush=True)
    try:
        return subprocess.run(argv, env=env, cwd=cwd, timeout=args.timeout).returncode
    except subprocess.TimeoutExpired:
        ui.fail(f"stopped after {args.timeout}s")
        return 124


def cmd_template(args: argparse.Namespace) -> int:
    if args.id in (None, "list"):
        ui.table([[k, v["label"], v["about"]] for k, v in containers.TEMPLATES.items()],
                 ["ID", "APP", "WHAT IT IS"])
        return 0
    if args.id not in containers.TEMPLATES:
        raise UserError(f"no template {args.id!r} — see: hoist template list")
    try:
        containers.docker()
    except containers.ContainerError as exc:
        raise UserError(str(exc)) from exc
    spec = containers.TEMPLATES[args.id]
    name = detect.slug(args.name or args.id)
    if config.get_app(config.load(), name):
        raise UserError(f"{name} already exists — pick another --name")
    folder = containers.template_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "compose.yml").write_text(containers.template_compose(args.id))
    ui.info(f"{ui.bold(name)} {ui.dim('·')} {spec['label']} {ui.dim('·')} {spec['image']}")
    try:
        containers.pull(spec["image"], log=ui.step)
    except containers.ContainerError as exc:
        raise UserError(str(exc)) from exc
    up_args = argparse.Namespace(
        target=str(folder), name=name, port=None, cmd=None, domain=args.domain, hostname=None,
        env=None, local=args.local, no_qr=args.no_qr, ascii=False, branch=None, build=None,
        no_sync=True, preview_of=None)
    code = cmd_up(up_args)
    with config.transaction() as state:
        if name in state["apps"]:
            state["apps"][name]["template"] = args.id
    return code


def _db_app(name: str) -> dict:
    app = config.get_app(config.load(), name)
    if app is None or app.get("kind") != "db":
        raise UserError(f"{name!r} is not a hoist database")
    return app


def cmd_backup(args: argparse.Namespace) -> int:
    app = _db_app(args.name)
    base = containers.backup_unit_base(args.name)
    if args.offsite:
        with config.transaction() as state:
            db = state["apps"][args.name]["db"]
            if args.offsite == "off":
                db.pop("offsite", None)
            else:
                db["offsite"] = args.offsite
        ui.ok("off-site copies off" if args.offsite == "off" else f"every backup is also copied to {args.offsite}")
        return 0
    if args.daily or args.no_schedule:
        if args.daily:
            svc, timer = containers.render_backup_units(
                args.name, sys.executable, str(Path(__file__).resolve().parent.parent),
                os.environ.get("HOIST_HOME"))
            service.UNIT_DIR.mkdir(parents=True, exist_ok=True)
            (service.UNIT_DIR / f"{base}.service").write_text(svc)
            (service.UNIT_DIR / f"{base}.timer").write_text(timer)
            service.systemctl("daemon-reload")
            service.systemctl("enable", "--now", f"{base}.timer")
            with config.transaction() as state:
                state["apps"][args.name]["db"]["daily_backup"] = True
            ui.ok(f"{args.name} is backed up every day; the newest {containers.BACKUP_KEEP} are kept")
        else:
            service.systemctl("disable", "--now", f"{base}.timer")
            for suffix in (".timer", ".service"):
                (service.UNIT_DIR / f"{base}{suffix}").unlink(missing_ok=True)
            service.systemctl("daemon-reload")
            with config.transaction() as state:
                state["apps"][args.name]["db"].pop("daily_backup", None)
            ui.ok(f"daily backups of {args.name} turned off (existing files kept)")
        return 0
    if args.list:
        rows = [[b["file"], f"{b['size'] / 1024:.1f} KB", time.strftime("%Y-%m-%d %H:%M", time.localtime(b["at"]))]
                for b in containers.list_backups(args.name)]
        if rows:
            ui.table(rows, ["FILE", "SIZE", "TAKEN"])
        else:
            ui.info("no backups yet")
        return 0
    if args.restore:
        target = containers.backup_dir(args.name) / Path(args.restore).name
        if not target.is_file():
            raise UserError(f"no backup called {args.restore!r} — see hoist backup {args.name} --list")
        ui.step(f"restoring {target.name} into {args.name}")
        containers.restore(args.name, app["db"], target, service.systemctl)
        ui.ok(f"{args.name} restored from {target.name}")
        notify.send(f"{args.name} restored from a backup", target.name, level="warn", app=args.name)
        return 0
    try:
        path = containers.backup(args.name, app["db"])
    except containers.ContainerError as exc:
        notify.send(f"Backup of {args.name} failed", str(exc), level="bad", app=args.name)
        raise UserError(str(exc)) from exc
    ui.ok(f"backed up {args.name} → {path} ({path.stat().st_size / 1024:.1f} KB)")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """Put a database's URL into an app's environment and restart the app."""
    with config.transaction() as state:
        db = config.get_app(state, args.db)
        app = config.get_app(state, args.app)
        if db is None or db.get("kind") != "db":
            raise UserError(f"{args.db!r} is not a hoist database")
        if app is None:
            raise UserError(f"no hoisted app called {args.app!r}")
        if app.get("adopted"):
            raise UserError(f"{args.app} was adopted; hoist does not manage its environment")
        var = args.var or db["db"]["env_var"]
        app.setdefault("env", {})[var] = db["db"]["url"]
        app["updated"] = config.now()
        service.write_unit(app)
    if not app.get("stopped"):
        service.restart(args.app)
    ui.ok(f"{args.app} now has {var} pointing at {args.db} (restarted)")
    return 0


def cmd_limit(args: argparse.Namespace) -> int:
    import re as _re
    with config.transaction() as state:
        app = config.get_app(state, args.name)
        if app is None:
            raise UserError(f"no hoisted app called {args.name!r}")
        if app.get("adopted"):
            raise UserError(f"{args.name} was adopted; hoist does not manage its process")
        if app.get("kind") == "compose":
            raise UserError("a Compose stack sets its limits per service in the compose file "
                            "(deploy.resources.limits) — hoist cannot cap the containers from outside")
        limits = dict(app.get("limits") or {})
        for key, value, pattern, example in (("memory", args.memory, service.LIMIT_MEMORY, "512M"),
                                             ("cpu", args.cpu, service.LIMIT_CPU, "50%")):
            if value is None:
                continue
            value = value.strip().upper()
            if value in ("", "NONE", "OFF"):
                limits.pop(key, None)
            elif not _re.match(pattern, value):
                raise UserError(f"--{key} expects something like {example}, got {value!r}")
            else:
                limits[key] = value
        if limits:
            app["limits"] = limits
        else:
            app.pop("limits", None)
        if app.get("kind") == "db":
            app["cmd"] = containers.database_command(args.name, app["db"], limits)
        service.write_unit(app)
    if not app.get("stopped"):
        service.restart(args.name)
    shown = ", ".join(f"{k} {v}" for k, v in limits.items()) or "no limits"
    ui.ok(f"{args.name}: {shown} (restarted)")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    state = config.load()
    if args.off:
        state.pop("notify", None)
        config.save(state)
        ui.ok("notifications off")
        return 0
    if args.url:
        if not args.url.startswith(("https://", "http://")):
            raise UserError("expected a webhook URL starting with https://")
        state["notify"] = {"url": args.url}
        config.save(state)
        ui.ok(f"notifications go to a {notify.kind(args.url)} webhook")
    current = (state.get("notify") or {}).get("url")
    if not current:
        ui.info("no webhook set — hoist notify <discord|slack|ntfy|telegram|any URL>")
        return 0
    if args.test or args.url:
        good, detail = notify.send("hoist is connected",
                                   "You will hear about deploys, failed builds and apps that stop answering.",
                                   level="ok")
        (ui.ok if good else ui.fail)(f"test message: {detail}")
        return 0 if good else 1
    ui.info(f"notifications go to a {notify.kind(current)} webhook")
    return 0


DASH_PORT = 7457
DASH_TUNNEL_NAME = "_dashboard"  # slug() can never produce "_", so no app collides


def _dash_unit_text(port: int, host: str) -> str:
    pkg_root = Path(__file__).resolve().parent.parent
    env = f"Environment=PYTHONPATH={pkg_root}\n"
    if os.environ.get("HOIST_HOME"):
        env += f"Environment=HOIST_HOME={os.environ['HOIST_HOME']}\n"
    return f"""[Unit]
Description=hoist dashboard
After=network-online.target

[Service]
Type=simple
{env}ExecStart={sys.executable} -m hoist dash --port {port} --host {host} --foreground
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def cmd_dash(args: argparse.Namespace) -> int:
    from .dash import server

    from .dash import team

    token = server.load_token(reset=args.reset_token)
    port = args.port
    base = (config.load().get("dash_url") or f"http://127.0.0.1:{port}").rstrip("/")
    if args.add_user:
        try:
            member = team.add(args.add_user)
        except team.TeamError as exc:
            raise UserError(str(exc)) from exc
        team.audit("owner", "add teammate", args.add_user.lower(), "from the CLI")
        ui.ok(f"{args.add_user} can sign in with this link (shown once):")
        ui.banner(f"{base}/?token={member}", f"hoist dash --remove-user {args.add_user} revokes it")
        return 0
    if args.remove_user:
        if not team.remove(args.remove_user):
            raise UserError(f"no teammate called {args.remove_user!r}")
        team.audit("owner", "remove teammate", args.remove_user, "from the CLI")
        ui.ok(f"{args.remove_user} is signed out and their link no longer works")
        return 0
    if args.users:
        people = team.users()
        ui.table([["owner", "owner", "—"]] + [[n, "member", i.get("added", "")] for n, i in sorted(people.items())],
                 ["NAME", "ROLE", "ADDED"])
        return 0

    if args.service or args.public:
        unit = service.UNIT_DIR / "hoist--dash.service"
        service.UNIT_DIR.mkdir(parents=True, exist_ok=True)
        unit.write_text(_dash_unit_text(port, args.host))
        service.systemctl("daemon-reload")
        result = service.systemctl("enable", "--now", "hoist--dash.service")
        if result.returncode != 0:
            raise UserError(result.stderr.strip() or "could not start the dashboard service")
        service.systemctl("restart", "hoist--dash.service")
        ui.ok(f"dashboard running as a service on 127.0.0.1:{port} (survives reboots)")
        url = f"http://127.0.0.1:{port}"
        if args.public:
            state = config.load()
            cf_path = tunnel.find_config()
            cf_text = cf_path.read_text()
            domain = _resolve_domain(state, None, cf_text)
            hostname = args.public if "." in args.public else f"{args.public}.{domain}"
            updated = _apply_ingress(cf_path, cf_text, DASH_TUNNEL_NAME, hostname, port)
            _publish(updated, hostname)
            url = f"https://{hostname}"
            with config.transaction() as st:
                st["dash_url"] = url
        ui.banner(f"{url}/?token={token}", "open this once; the browser remembers you")
        return 0

    if args.stop:
        service.systemctl("disable", "--now", "hoist--dash.service")
        (service.UNIT_DIR / "hoist--dash.service").unlink(missing_ok=True)
        service.systemctl("daemon-reload")
        ui.ok("dashboard service stopped and removed")
        return 0

    if not detect.port_is_free(port):
        if args.foreground:
            raise UserError(f"port {port} is already in use")
        ui.info(f"the dashboard is already running on port {port}")
        ui.banner(f"http://127.0.0.1:{port}/?token={token}")
        return 0
    httpd = server.serve(args.host, port, token)
    login = f"http://127.0.0.1:{port}/?token={token}"
    if not args.foreground:
        ui.banner(login, "Ctrl+C to stop   ·   hoist dash --service keeps it running")
        if args.open:
            subprocess.Popen(["xdg-open", login], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# --- argument parsing ---------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoist",
        description="Put a local app on a public HTTPS URL, with one command.",
    )
    parser.add_argument("--version", action="version", version=f"hoist {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_qr_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--no-qr", action="store_true", help="do not print a QR code")
        p.add_argument("--ascii", action="store_true", help="QR without ANSI colour")

    up = sub.add_parser("up", help="hoist a directory or git repo onto a public URL")
    up.add_argument("target", nargs="?",
                    help="project directory (default: cwd) or git URL, e.g. github.com/team/app")
    up.add_argument("--name", help="app name and subdomain label")
    up.add_argument("--port", type=int, help="port to run on (default: auto)")
    up.add_argument("--cmd", help="start command; $PORT is substituted")
    up.add_argument("--domain", help="zone to put the subdomain under")
    up.add_argument("--hostname", help="full hostname, overriding <name>.<domain>")
    up.add_argument("--env", action="append", metavar="K=V", help="extra env var")
    up.add_argument("--local", action="store_true", help="LAN URL only, no tunnel")
    up.add_argument("--branch", help="git branch to deploy (repo URLs only)")
    up.add_argument("--build", help="build command for a repo, overriding detection")
    up.add_argument("--preview-of", help=argparse.SUPPRESS)
    up.add_argument("--no-sync", action="store_true",
                    help="deploy a repo once, without redeploying on push")
    add_qr_flags(up)
    up.set_defaults(func=cmd_up)

    share = sub.add_parser("share", help="serve a folder of files publicly")
    share.add_argument("path", nargs="?", default=".", help="directory to serve")
    share.add_argument("--name", help="app name and subdomain label")
    share.add_argument("--port", type=int)
    share.add_argument("--domain")
    share.add_argument("--hostname")
    share.add_argument("--env", action="append", metavar="K=V")
    share.add_argument("--local", action="store_true")
    add_qr_flags(share)
    share.set_defaults(func=cmd_share)

    ls = sub.add_parser("ls", help="list hoisted apps")
    ls.set_defaults(func=cmd_ls)

    down = sub.add_parser("down", help="stop an app and remove its ingress rule")
    down.add_argument("name")
    down.add_argument("--purge", action="store_true", help="also delete a database's or stack's data volumes")
    down.set_defaults(func=cmd_down)

    sync = sub.add_parser("sync", help="redeploy a git-backed app if its branch moved")
    sync.add_argument("name")
    sync.add_argument("--force", action="store_true", help="rebuild even if up to date")
    sync.add_argument("--to", metavar="SHA", help="roll back to this commit (pauses auto-deploy)")
    sync.set_defaults(func=cmd_sync)

    for verb, func, text in (
        ("pause", cmd_pause, "stop redeploying a repo app on push"),
        ("resume", cmd_resume, "redeploy a repo app on push again"),
        ("stop", cmd_stop, "stop an app's process, keeping its config and URL"),
        ("start", cmd_start, "start a stopped app again"),
    ):
        p = sub.add_parser(verb, help=text)
        p.add_argument("name")
        p.set_defaults(func=func)

    logs = sub.add_parser("logs", help="show an app's logs")
    logs.add_argument("name")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("--deploys", action="store_true",
                      help="show the push-to-deploy log instead of the app's")
    logs.set_defaults(func=cmd_logs)

    restart = sub.add_parser("restart", help="restart an app")
    restart.add_argument("name")
    restart.set_defaults(func=cmd_restart)

    url = sub.add_parser("url", help="print an app's URL")
    url.add_argument("name")
    url.set_defaults(func=cmd_url)

    qr_cmd = sub.add_parser("qr", help="print an app's QR code")
    qr_cmd.add_argument("name")
    qr_cmd.add_argument("--ascii", action="store_true")
    qr_cmd.set_defaults(func=cmd_qr)

    adopt = sub.add_parser("adopt", help="expose an already-running port")
    adopt.add_argument("name")
    adopt.add_argument("--port", type=int, required=True)
    adopt.add_argument("--domain")
    adopt.add_argument("--hostname")
    add_qr_flags(adopt)
    adopt.set_defaults(func=cmd_adopt)

    db = sub.add_parser("db", help="start a database: postgres, mysql, redis or mongo")
    db.add_argument("engine", choices=sorted(containers.ENGINES))
    db.add_argument("--name", help="app name (default: the engine)")
    db.add_argument("--port", type=int, help="host port on 127.0.0.1 (default: free)")
    db.add_argument("--image", help="override the image, e.g. postgres:17")
    db.set_defaults(func=cmd_db)

    ex = sub.add_parser("exec", help="run one command in an app's environment")
    ex.add_argument("name")
    ex.add_argument("command", nargs=argparse.REMAINDER, help="the command (quote it, or put it after --)")
    ex.add_argument("--service", help="Compose service to run in (default: the first)")
    ex.add_argument("--timeout", type=int, default=600)
    ex.set_defaults(func=cmd_exec)

    tpl = sub.add_parser("template", help="one-click apps: uptime-kuma, n8n, ntfy, excalidraw...")
    tpl.add_argument("id", nargs="?", help="template id, or `list`")
    tpl.add_argument("--name", help="app name (default: the template id)")
    tpl.add_argument("--local", action="store_true", help="LAN URL only, no tunnel")
    tpl.add_argument("--domain")
    add_qr_flags(tpl)
    tpl.set_defaults(func=cmd_template)

    bk = sub.add_parser("backup", help="back up, list or restore a database")
    bk.add_argument("name")
    bk.add_argument("--list", action="store_true", help="list backups")
    bk.add_argument("--restore", metavar="FILE", help="load this backup into the database")
    bk.add_argument("--daily", action="store_true", help="back up every day")
    bk.add_argument("--no-schedule", action="store_true", help="stop daily backups")
    bk.add_argument("--offsite", metavar="TARGET",
                    help="also copy every backup to an rclone remote (gdrive:hoist) or a folder; 'off' stops it")
    bk.set_defaults(func=cmd_backup)

    link = sub.add_parser("link", help="give an app a database's URL")
    link.add_argument("db")
    link.add_argument("app")
    link.add_argument("--var", help="variable name (default: DATABASE_URL, REDIS_URL...)")
    link.set_defaults(func=cmd_link)

    prev = sub.add_parser("previews", help="give every branch its own preview app")
    prev.add_argument("name")
    prev.add_argument("state", choices=["on", "off"])
    prev.set_defaults(func=cmd_previews)

    lim = sub.add_parser("limit", help="cap an app's memory or CPU")
    lim.add_argument("name")
    lim.add_argument("--memory", help="e.g. 512M or 2G; 'off' removes it")
    lim.add_argument("--cpu", help="e.g. 50%% of one core, 200%% for two; 'off' removes it")
    lim.set_defaults(func=cmd_limit)

    note = sub.add_parser("notify", help="send deploy and downtime alerts to a webhook")
    note.add_argument("url", nargs="?", help="Discord, Slack, ntfy, Telegram or any webhook URL")
    note.add_argument("--test", action="store_true", help="send a test message")
    note.add_argument("--off", action="store_true", help="stop sending notifications")
    note.set_defaults(func=cmd_notify)

    dash = sub.add_parser("dash", help="open the web dashboard")
    dash.add_argument("--port", type=int, default=DASH_PORT)
    dash.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1)")
    dash.add_argument("--open", action="store_true", help="open it in the browser")
    dash.add_argument("--service", action="store_true", help="keep it running as a user service")
    dash.add_argument("--public", metavar="NAME", help="also publish it, e.g. --public dash")
    dash.add_argument("--stop", action="store_true", help="stop and remove the dashboard service")
    dash.add_argument("--reset-token", action="store_true", help="sign every browser out")
    dash.add_argument("--add-user", metavar="NAME", help="give a teammate their own sign-in link")
    dash.add_argument("--remove-user", metavar="NAME", help="revoke a teammate's link")
    dash.add_argument("--users", action="store_true", help="list who has access")
    dash.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    dash.set_defaults(func=cmd_dash)

    doctor = sub.add_parser("doctor", help="check the local setup")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except UserError as exc:
        ui.fail(str(exc))
        return 1
    except (tunnel.TunnelError, service.ServiceError, detect.DetectionError,
            source.SourceError, config.StateError, containers.ContainerError) as exc:
        ui.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
