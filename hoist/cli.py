"""hoist -- put a local app on a public HTTPS URL, with one command."""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

from . import config, detect, qr, service, tunnel, ui

__version__ = "0.1.0"

HEALTH_TIMEOUT = 20.0
CRASH_LOOP_RESTARTS = 2


class UserError(Exception):
    """Something the user can fix; printed without a traceback."""


# --- helpers ------------------------------------------------------------------


def _wait_for_healthy(name: str, port: int, timeout: float = HEALTH_TIMEOUT) -> str:
    """Wait for the port to open. Returns "up", "failed" or "timeout".

    The unit state is watched alongside the port so a crashed app is reported
    in a second or two instead of after the full timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if detect.port_in_use(port):
            return "up"
        if service.status(name) == "failed":
            return "failed"
        if service.n_restarts(name) >= CRASH_LOOP_RESTARTS:
            return "crashing"
        time.sleep(0.25)
    return "timeout"


def _report_health(name: str, port: int) -> str:
    health = _wait_for_healthy(name, port)
    if health == "up":
        ui.ok(f"listening on 127.0.0.1:{port}")
        return health
    if health == "crashing":
        ui.fail(f"{name} keeps crashing on startup")
    elif health == "failed":
        ui.fail(f"{name} failed to start")
    else:
        ui.warn(f"nothing listening on port {port} after {int(HEALTH_TIMEOUT)}s")
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

    target = Path(args.target).expanduser().resolve() if args.target else Path.cwd()
    if not target.is_dir():
        raise UserError(f"not a directory: {target}")

    if args.cmd:
        command, how = args.cmd, "custom command"
    else:
        command, how = detect.detect_command(target)

    name = detect.slug(args.name or target.name)
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

    health = _report_health(name, port)

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
        listening = detect.port_in_use(app["port"])
        if listening and unit_state in ("active", "adopted"):
            health = ui.green("up")
        elif unit_state == "active":
            health = ui.yellow("starting")
        elif unit_state == "failed":
            health = ui.red("failed")
        else:
            health = ui.dim(unit_state)
        rows.append([name, str(app["port"]), health, _app_url(app)])
    ui.table(rows, ["NAME", "PORT", "STATE", "URL"])
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")

    if not app.get("adopted"):
        service.stop_and_remove(args.name)
        ui.ok("service stopped and removed")

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


def cmd_logs(args: argparse.Namespace) -> int:
    state = config.load()
    app = config.get_app(state, args.name)
    if app is None:
        raise UserError(f"no hoisted app called {args.name!r}")
    if app.get("adopted"):
        raise UserError(f"{args.name} was adopted; hoist does not manage its logs")
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


def cmd_doctor(args: argparse.Namespace) -> int:
    problems = 0

    if service.have_systemd():
        ui.ok("systemd --user available")
    else:
        ui.fail("systemd --user unavailable — hoist cannot manage services")
        problems += 1

    if service.lingering_enabled():
        ui.ok("lingering enabled (services survive logout)")
    else:
        ui.warn("lingering off — services stop when you log out")
        ui.step(f"fix: sudo loginctl enable-linger {os.environ.get('USER', '$USER')}")

    if tunnel.have_cloudflared():
        ui.ok("cloudflared installed")
    else:
        ui.warn("cloudflared not installed — only --local URLs will work")

    try:
        cf_path = tunnel.find_config()
        text = cf_path.read_text()
        ui.ok(f"tunnel config: {cf_path}")
        tid = tunnel.tunnel_id(text)
        if tid:
            ui.ok(f"tunnel id: {tid}")
        else:
            ui.warn("no `tunnel:` id in config")
        hosts = tunnel.hostnames(text)
        ui.ok(f"{len(hosts)} ingress hostname(s): {', '.join(hosts) or '—'}")
        domain = tunnel.default_domain(text)
        if domain:
            ui.ok(f"default domain: {domain}")
        else:
            ui.warn("no domain inferred — pass --domain once")
        if tunnel.needs_sudo(cf_path):
            ui.warn(f"{cf_path} needs sudo to edit (you will be prompted)")
    except tunnel.TunnelError as exc:
        ui.warn(str(exc))

    running = tunnel.daemon_unit()
    if running:
        _unit, is_system = running
        ui.ok(f"cloudflared running as {'system' if is_system else 'user'} unit")
    else:
        ui.warn("cloudflared is not running as a service")

    state = config.load()
    ui.ok(f"{len(state['apps'])} app(s) tracked in {config.STATE_FILE}")
    return 1 if problems else 0


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

    up = sub.add_parser("up", help="hoist a directory onto a public URL")
    up.add_argument("target", nargs="?", help="project directory (default: cwd)")
    up.add_argument("--name", help="app name and subdomain label")
    up.add_argument("--port", type=int, help="port to run on (default: auto)")
    up.add_argument("--cmd", help="start command; $PORT is substituted")
    up.add_argument("--domain", help="zone to put the subdomain under")
    up.add_argument("--hostname", help="full hostname, overriding <name>.<domain>")
    up.add_argument("--env", action="append", metavar="K=V", help="extra env var")
    up.add_argument("--local", action="store_true", help="LAN URL only, no tunnel")
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
    down.set_defaults(func=cmd_down)

    logs = sub.add_parser("logs", help="show an app's logs")
    logs.add_argument("name")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.add_argument("-f", "--follow", action="store_true")
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
    except (tunnel.TunnelError, service.ServiceError, detect.DetectionError) as exc:
        ui.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
