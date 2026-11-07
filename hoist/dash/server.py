"""`hoist dash`: a web dashboard for everything hoist runs.

Standard library only. One token guards everything, because the dashboard
is meant to be published through the same tunnel as the apps -- and behind
a tunnel every request arrives from 127.0.0.1, so "local means trusted" is
not a rule this server can use.

Security model:
- the token lives in the state dir (0600) and is shown once by `hoist dash`;
- the browser exchanges it for an HttpOnly, SameSite=Strict cookie;
- every state-changing request must also carry `X-Hoist: 1`, which a
  cross-site form cannot set, so the cookie alone is not enough to act.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__ as VERSION
from .. import config, detect, notify, qr, service, source
from . import collect, jobs

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "hoist_dash"
TOKEN_FILE_NAME = "dash-token"
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def token_path() -> Path:
    return config.STATE_DIR / TOKEN_FILE_NAME


def load_token(reset: bool = False) -> str:
    path = token_path()
    if path.exists() and not reset:
        value = path.read_text().strip()
        if value:
            return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    return value


class Dashboard:
    def __init__(self, token: str) -> None:
        self.token = token
        self.monitor = collect.Monitor()
        self.jobs = jobs.Jobs(on_finish=lambda _job: self.monitor.nudge())
        self.monitor.start()

    # -- helpers ---------------------------------------------------------------

    def app(self, name: str) -> dict | None:
        return config.get_app(config.load(), name)

    def app_detail(self, name: str) -> dict | None:
        state = config.load()
        app = config.get_app(state, name)
        if app is None:
            return None
        row = next((r for r in self.monitor.get().get("apps", []) if r["name"] == name), None)
        repo = app.get("repo") or {}
        return {
            "row": row,
            "env": app.get("env") or {},
            "history": repo.get("history", []),
            "series": self.monitor.series(name),
            "probes": self.monitor.probes_for(name),
            "jobs": [j for j in self.jobs.recent() if j["app"] == name][:10],
            "sync_unit": f"{source.sync_unit_base(name)}.service" if repo else None,
            "unit": service.unit_name(name),
        }


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"hoist-dash/{VERSION}"
        protocol_version = "HTTP/1.1"

        # Quiet by default; errors still reach stderr.
        def log_message(self, fmt, *args):  # noqa: N802
            if os.environ.get("HOIST_DASH_DEBUG"):
                super().log_message(fmt, *args)

        # -- plumbing -------------------------------------------------------------

        def _secure(self) -> bool:
            proto = self.headers.get("X-Forwarded-Proto", "")
            return proto == "https" or bool(self.headers.get("Cf-Visitor"))

        def _authed(self) -> bool:
            cookies = self.headers.get("Cookie", "")
            for part in cookies.split(";"):
                key, _, value = part.strip().partition("=")
                if key == COOKIE and hmac.compare_digest(value, dash.token):
                    return True
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], dash.token):
                return True
            return False

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _error(self, code: int, message: str) -> None:
            self._json({"error": message}, code)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ValueError("request too large")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def _static(self, rel: str) -> None:
            path = (STATIC / rel).resolve()
            if STATIC not in path.parents and path != STATIC or not path.is_file():
                self._error(404, "not found")
                return
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".woff2":
                ctype = "font/woff2"
            extra = {"Cache-Control": "public, max-age=86400"} if "fonts/" in rel else None
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", (extra or {}).get("Cache-Control", "no-cache"))
            self.end_headers()
            self.wfile.write(body)

        def _sse_start(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def _sse(self, data, event: str | None = None) -> None:
            chunk = (f"event: {event}\n" if event else "") + f"data: {json.dumps(data)}\n\n"
            self.wfile.write(chunk.encode())
            self.wfile.flush()

        # -- routing ----------------------------------------------------------------

        def do_HEAD(self):  # noqa: N802
            self.do_GET()

        def do_GET(self):  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            path, query = url.path, urllib.parse.parse_qs(url.query)

            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/favicon.svg":
                return self._static("favicon.svg")

            if path in ("/", "/index.html"):
                given = (query.get("token") or [""])[0]
                if given:
                    if hmac.compare_digest(given, dash.token):
                        cookie = (f"{COOKIE}={dash.token}; Path=/; HttpOnly; SameSite=Strict;"
                                  f" Max-Age=2592000" + ("; Secure" if self._secure() else ""))
                        nxt = (query.get("next") or ["/"])[0]
                        if not nxt.startswith("/") or nxt.startswith("//"):
                            nxt = "/"
                        return self._send(303, b"", "text/plain",
                                          {"Location": nxt, "Set-Cookie": cookie})
                    time.sleep(0.5)
                page = "index.html" if self._authed() else "login.html"
                return self._static(page)

            if not path.startswith("/api/"):
                # client-side routes fall back to the app shell
                return self._static("index.html" if self._authed() else "login.html")
            if not self._authed():
                return self._error(401, "sign in first")

            try:
                return self._api_get(path, query)
            except (BrokenPipeError, ConnectionResetError):
                return None

        def do_POST(self):  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            if path == "/api/login":
                try:
                    given = str(self._body().get("token", ""))
                except ValueError:
                    given = ""
                if not given or not hmac.compare_digest(given, dash.token):
                    time.sleep(0.75)
                    return self._error(401, "that token is not right")
                cookie = (f"{COOKIE}={dash.token}; Path=/; HttpOnly; SameSite=Strict;"
                          f" Max-Age=2592000" + ("; Secure" if self._secure() else ""))
                return self._send(200, b'{"ok":true}', "application/json", {"Set-Cookie": cookie})
            if path == "/api/logout":
                return self._send(200, b'{"ok":true}', "application/json",
                                  {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; SameSite=Strict"})
            if not self._authed():
                return self._error(401, "sign in first")
            if self.headers.get("X-Hoist") != "1":
                return self._error(403, "missing X-Hoist header")
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._error(400, str(exc))
            try:
                return self._api_post(path, body)
            except ValueError as exc:
                return self._error(400, str(exc))

        # -- GET endpoints --------------------------------------------------------

        def _api_get(self, path: str, query: dict) -> None:
            if path == "/api/state":
                snap = dash.monitor.get()
                snap["jobs"] = dash.jobs.recent()[:12]
                snap["version"] = VERSION
                return self._json(snap)

            if path == "/api/deployments":
                state = config.load()
                rows = []
                for name, app in state.get("apps", {}).items():
                    repo = app.get("repo") or {}
                    for entry in repo.get("history", []):
                        rows.append(dict(entry, app=name, live=entry.get("sha") == repo.get("sha")
                                         and entry is next((h for h in repo["history"]
                                                            if h.get("status") != "failed"), None)))
                rows.sort(key=lambda r: r.get("at", ""), reverse=True)
                return self._json({"deployments": rows[:200]})

            if path == "/api/notify":
                url = (config.load().get("notify") or {}).get("url")
                return self._json({"url": url, "kind": notify.kind(url) if url else None})

            if path == "/api/metrics":
                return self._json({"system": dash.monitor.series()})

            if path == "/api/doctor":
                from ..cli import doctor_checks
                return self._json({"checks": doctor_checks()})

            if path == "/api/qr.svg":
                text = (query.get("text") or [""])[0][:300]
                if not text:
                    return self._error(400, "text is required")
                return self._send(200, qr_svg(text).encode(), "image/svg+xml")

            if path == "/api/routes":
                rules = dash.monitor.get().get("routes", {}).get("rules", [])
                data = []
                for rule in rules:
                    key = (f"route:{rule['hostname']}" if not rule.get("managed")
                           else rule.get("managed"))
                    data.append(dict(rule, probes=dash.monitor.probes_for(key or "")))
                return self._json({"rules": data})

            match = re.fullmatch(r"/api/jobs/(j\d+)(/stream)?", path)
            if match:
                job = dash.jobs.get(match.group(1))
                if job is None:
                    return self._error(404, "no such job")
                if not match.group(2):
                    return self._json(job.as_dict(with_lines=True))
                return self._stream_job(job)

            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)(/[a-z/]+)?", path)
            if match:
                name, rest = match.group(1), match.group(2) or ""
                if rest == "":
                    detail = dash.app_detail(name)
                    return self._json(detail) if detail else self._error(404, "no such app")
                app = dash.app(name)
                if app is None:
                    return self._error(404, "no such app")
                if rest in ("/logs", "/logs/stream"):
                    kind = (query.get("kind") or ["app"])[0]
                    if app.get("adopted"):
                        return self._error(409, "hoist does not manage this process, so it has no logs here")
                    unit = (f"{source.sync_unit_base(name)}.service" if kind == "deploys"
                            else service.unit_name(name))
                    if rest == "/logs":
                        lines = int((query.get("n") or ["400"])[0])
                        return self._json({"unit": unit, "lines": collect.journal(unit, min(lines, 5000))})
                    return self._stream_journal(unit)
            return self._error(404, "unknown endpoint")

        def _stream_job(self, job) -> None:
            self._sse_start()
            sent = 0
            try:
                while True:
                    with job.changed:
                        if sent >= len(job.lines) and job.status == "running":
                            job.changed.wait(15)
                        lines = job.lines[sent:]
                        status = job.status
                    for line in lines:
                        self._sse(line, "line")
                    sent += len(lines)
                    if status != "running" and sent >= len(job.lines):
                        self._sse(job.as_dict(), "done")
                        return
                    if not lines:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def _stream_journal(self, unit: str) -> None:
            self._sse_start()
            proc = subprocess.Popen(
                ["journalctl", "--user", "-u", unit, "-f", "-n", "0", "-o", "json",
                 "--output-fields=MESSAGE,PRIORITY,_PID"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace",
            )
            alive = threading.Event()
            alive.set()

            def heartbeat():
                while alive.is_set():
                    time.sleep(15)
                    try:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                    except OSError:
                        alive.clear()
                        proc.kill()
                        return

            threading.Thread(target=heartbeat, daemon=True).start()
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    entry = collect.parse_journal_line(line)
                    if entry:
                        self._sse(entry, "line")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                alive.clear()
                proc.kill()

        # -- POST endpoints -------------------------------------------------------

        def _api_post(self, path: str, body: dict) -> None:
            if path == "/api/apps":
                return self._create(body)
            if path == "/api/notify":
                url = str(body.get("url") or "").strip()
                if url and (not url.startswith(("https://", "http://")) or len(url) > 2000):
                    raise ValueError("expected a webhook URL starting with https://")
                with config.transaction() as state:
                    if url:
                        state["notify"] = {"url": url}
                    else:
                        state.pop("notify", None)
                return self._json({"url": url or None, "kind": notify.kind(url) if url else None})
            if path == "/api/notify/test":
                good, detail = notify.send("hoist is connected",
                                           "You will hear about deploys, failed builds and apps that stop answering.",
                                           level="ok")
                return self._json({"ok": good, "detail": detail}, 200 if good else 502)

            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/([a-z-]+)", path)
            if not match:
                return self._error(404, "unknown endpoint")
            name, action = match.groups()
            app = dash.app(name)
            if app is None:
                return self._error(404, "no such app")
            running = dash.jobs.busy(name)
            long_actions = {"redeploy", "rollback", "delete"}
            if action in long_actions and running:
                return self._json({"error": "another action is still running", "job": running.as_dict()}, 409)

            if action == "redeploy":
                if not app.get("repo"):
                    return self._error(400, "only apps deployed from git can be redeployed")
                job = dash.jobs.start(f"Redeploy {name}", ["sync", name, "--force"], name)
                return self._json({"job": job.as_dict()}, 202)
            if action == "rollback":
                sha = str(body.get("sha", ""))
                if not _SHA.match(sha):
                    return self._error(400, "a commit SHA is required")
                job = dash.jobs.start(f"Roll back {name} to {sha[:7]}", ["sync", name, "--to", sha], name)
                return self._json({"job": job.as_dict()}, 202)
            if action == "delete":
                job = dash.jobs.start(f"Take down {name}", ["down", name], name)
                return self._json({"job": job.as_dict()}, 202)

            if action in ("restart", "stop", "start", "pause", "resume"):
                return self._quick(name, app, action)
            if action == "env":
                return self._set_env(name, body)
            if action == "limits":
                args = ["limit", name]
                for key in ("memory", "cpu"):
                    value = str(body.get(key, "") or "").strip() or "off"
                    if value != "off" and not re.match(getattr(service, f"LIMIT_{key.upper()}"), value.upper()):
                        raise ValueError(f"{key}: use something like {'512M' if key == 'memory' else '50%'}")
                    args += [f"--{key}", value]
                return self._run_quick(args)
            return self._error(404, "unknown action")

        def _quick(self, name: str, app: dict, action: str) -> None:
            if app.get("adopted") and action in ("restart", "stop", "start"):
                return self._error(409, "hoist does not manage this process")
            if action in ("pause", "resume") and not app.get("repo"):
                return self._error(400, "auto-deploy only applies to apps deployed from git")
            return self._run_quick([action, name])

        def _run_quick(self, args: list[str]) -> None:
            action = args[0]
            proc = subprocess.run(
                [os.sys.executable, "-m", "hoist", *args], capture_output=True, text=True,
                env=dict(os.environ, NO_COLOR="1",
                         PYTHONPATH=str(jobs._PKG_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")),
                timeout=60,
            )
            dash.monitor.nudge()
            output = jobs._ANSI.sub("", (proc.stdout + proc.stderr).strip())
            if proc.returncode != 0:
                return self._json({"error": output.splitlines()[-1] if output else f"{action} failed"}, 500)
            return self._json({"ok": True, "output": output})

        def _set_env(self, name: str, body: dict) -> None:
            env = body.get("env")
            if not isinstance(env, dict):
                raise ValueError("env must be an object of KEY: value")
            clean: dict[str, str] = {}
            for key, value in env.items():
                key = str(key).strip()
                if not _ENV_KEY.match(key):
                    raise ValueError(f"{key!r} is not a valid variable name")
                if key in ("PORT", "HOIST_APP"):
                    raise ValueError(f"{key} is set by hoist")
                value = "" if value is None else str(value)
                if "\n" in value or "\r" in value:
                    raise ValueError(f"{key} has a line break; use one line")
                clean[key] = value
            with config.transaction() as state:
                app = config.get_app(state, name)
                if app is None:
                    return self._error(404, "no such app")
                if app.get("adopted"):
                    return self._error(409, "hoist does not manage this process, so it cannot set its environment")
                app["env"] = clean
                app["updated"] = config.now()
                service.write_unit(app)
            restarted = False
            if not app.get("stopped") and body.get("restart", True):
                service.restart(name)
                restarted = True
            dash.monitor.nudge()
            return self._json({"ok": True, "restarted": restarted, "count": len(clean)})

        def _create(self, body: dict) -> None:
            target = str(body.get("target", "")).strip()
            if not target:
                raise ValueError("a git URL or a folder is required")
            name = str(body.get("name", "")).strip().lower()
            if name and not _NAME.match(name):
                raise ValueError("name: lowercase letters, digits and dashes, up to 40")
            if not source.is_repo_url(target):
                folder = Path(target).expanduser()
                if not folder.is_dir():
                    raise ValueError(f"{target} is not a git URL or a folder on this machine")
                target = str(folder.resolve())
            args = ["up", target, "--no-qr"]
            if name:
                args += ["--name", name]
            for key, flag in (("branch", "--branch"), ("build", "--build"), ("cmd", "--cmd")):
                value = str(body.get(key, "") or "").strip()
                if value:
                    if "\n" in value:
                        raise ValueError(f"{key} must be one line")
                    args += [flag, value]
            if body.get("local"):
                args.append("--local")
            for key, value in (body.get("env") or {}).items():
                if not _ENV_KEY.match(str(key)):
                    raise ValueError(f"{key!r} is not a valid variable name")
                args += ["--env", f"{key}={value}"]
            app_name = name or detect.slug(source.repo_name(target) if source.is_repo_url(target)
                                           else Path(target).name)
            if dash.jobs.busy(app_name):
                return self._error(409, f"{app_name} is already being deployed")
            job = dash.jobs.start(f"Deploy {app_name}", args, app_name)
            return self._json({"job": job.as_dict(), "app": app_name}, 202)

    return Handler


def qr_svg(text: str) -> str:
    matrix = qr.encode(text)
    size = len(matrix)
    quiet = 2
    full = size + quiet * 2
    cells = "".join(
        f"M{x + quiet} {y + quiet}h1v1h-1z"
        for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" '
            f'shape-rendering="crispEdges"><rect width="{full}" height="{full}" fill="#fff"/>'
            f'<path d="{cells}" fill="#000"/></svg>')


def serve(host: str, port: int, token: str) -> ThreadingHTTPServer:
    dash = Dashboard(token)
    httpd = ThreadingHTTPServer((host, port), make_handler(dash))
    httpd.daemon_threads = True
    return httpd
