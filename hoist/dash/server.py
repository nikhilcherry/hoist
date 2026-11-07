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

import hashlib
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
from .. import config, containers, detect, notify, qr, service, source
from . import collect, fleet, jobs, team, terminal

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
        self.seen: dict[str, float] = {}
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

        def _token(self) -> str:
            for part in self.headers.get("Cookie", "").split(";"):
                key, _, value = part.strip().partition("=")
                if key == COOKIE and value:
                    return value
            auth = self.headers.get("Authorization", "")
            return auth[7:] if auth.startswith("Bearer ") else ""

        def _authed(self) -> bool:
            self.user = team.identify(self._token(), dash.token)
            if self.user:
                dash.seen[self.user] = time.time()
            return self.user is not None

        def _cookie(self, token: str) -> str:
            return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
                    + ("; Secure" if self._secure() else ""))

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
            raw = getattr(self, "_raw", b"")
            if len(raw) > 1_000_000:
                raise ValueError("request too large")
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
                    if team.identify(given, dash.token):
                        cookie = self._cookie(given)
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

            remote = re.fullmatch(r"/api/remote/([a-z0-9-]+)/(.+)", path)
            if remote:
                return self._proxy("GET", remote.group(1), remote.group(2), url.query)
            if path == "/api/servers":
                local = dash.monitor.get()
                apps = [a for a in local.get("apps", []) if a.get("kind") != "db"]
                broken = [a["name"] for a in apps if a.get("status") in ("down", "failed")
                          or (a.get("public") and a.get("probe") and not a["probe"].get("up"))]
                return self._json({"local": {"host": local.get("host"), "apps": len(apps), "broken": broken,
                                             "system": local.get("system")},
                                   "servers": fleet.summary() if self.user == team.OWNER else []})
            try:
                return self._api_get(path, query)
            except (BrokenPipeError, ConnectionResetError):
                return None

        def do_POST(self):  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            # Read the body before anything can answer early. On a keep-alive
            # connection an unread body is parsed as the start of the NEXT
            # request, so a rejected click used to corrupt the one after it.
            length = int(self.headers.get("Content-Length") or 0)
            if length > 5_000_000:
                self.close_connection = True
                return self._error(413, "request too large")
            self._raw = self.rfile.read(length) if length else b""
            match = re.fullmatch(r"/hooks/([a-z0-9-]+)", path)
            if match:
                return self._webhook(match.group(1))
            if path == "/api/login":
                try:
                    given = str(self._body().get("token", ""))
                except ValueError:
                    given = ""
                if not team.identify(given, dash.token):
                    time.sleep(0.75)
                    return self._error(401, "that token is not right")
                cookie = self._cookie(given)
                return self._send(200, b'{"ok":true}', "application/json", {"Set-Cookie": cookie})
            if path == "/api/logout":
                return self._send(200, b'{"ok":true}', "application/json",
                                  {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; SameSite=Strict"})
            if not self._authed():
                return self._error(401, "sign in first")
            if self.headers.get("X-Hoist") != "1":
                return self._error(403, "missing X-Hoist header")
            remote = re.fullmatch(r"/api/remote/([a-z0-9-]+)/(.+)", path)
            if remote:
                return self._proxy("POST", remote.group(1), remote.group(2), url.query)
            need = _required(path)
            if need is not None and not team.allows(self.user, *need):
                return self._error(403, _denied(self.user, *need))
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._error(400, str(exc))
            try:
                return self._api_post(path, body)
            except (ValueError, team.TeamError) as exc:
                return self._error(400, str(exc))

        # -- GET endpoints --------------------------------------------------------

        def _api_get(self, path: str, query: dict) -> None:
            if path == "/api/state":
                snap = dash.monitor.get()
                seen = team.scope(self.user)
                if seen is not None:
                    snap = dict(snap, apps=[a for a in snap.get("apps", []) if a["name"] in seen])
                snap = redact_state(snap, lambda n: team.allows(self.user, "secrets", n))
                snap["jobs"] = [j for j in dash.jobs.recent()[:40]
                                if seen is None or j.get("app") in seen][:12]
                snap["version"] = VERSION
                snap["me"] = self.user
                snap["role"] = team.role_of(self.user)
                access = team.access(self.user) or {}
                snap["can"] = sorted(access.get("caps", ()))
                snap["scope"] = sorted(seen) if seen is not None else None
                return self._json(snap)

            if path == "/api/deployments":
                state = config.load()
                rows = []
                seen = team.scope(self.user)
                for name, app in state.get("apps", {}).items():
                    if seen is not None and name not in seen:
                        continue
                    repo = app.get("repo") or {}
                    for entry in repo.get("history", []):
                        rows.append(dict(entry, app=name, live=entry.get("sha") == repo.get("sha")
                                         and entry is next((h for h in repo["history"]
                                                            if h.get("status") != "failed"), None)))
                rows.sort(key=lambda r: r.get("at", ""), reverse=True)
                return self._json({"deployments": rows[:200]})

            if path == "/api/notify":
                url = (config.load().get("notify") or {}).get("url")
                if url and not team.allows(self.user, "secrets"):
                    url = _URL_SECRET.sub(r"\1••••••@", url)
                return self._json({"url": url, "kind": notify.kind(url) if url else None})

            if path == "/api/templates":
                return self._json({"templates": [{"id": k, "label": v["label"], "about": v["about"],
                                                  "image": v["image"], "user": bool(v.get("user"))}
                                                 for k, v in containers.catalogue().items()],
                                   "docker": containers.available()})

            if path == "/api/team":
                people = [{"name": team.OWNER, "role": "owner", "added": None,
                           "seen": dash.seen.get(team.OWNER)}]
                for name, info in sorted(team.users().items()):
                    people.append({"name": name, "role": info.get("role", "member"), "added": info.get("added"),
                                   "seen": dash.seen.get(name), "can": info.get("can", []),
                                   "apps": info.get("apps", []), "stages": info.get("stages", [])})
                seen = team.scope(self.user)
                audit = team.audit_tail(int((query.get("n") or ["200"])[0]))
                if seen is not None:  # a scoped person sees only their own apps' history
                    audit = [r for r in audit if r.get("target") in seen]
                    people = [p for p in people if p["name"] == self.user]
                return self._json({"me": self.user, "people": people, "audit": audit,
                                   "caps": team.CAPS, "stages": list(team.STAGES)})

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
                seen = team.scope(self.user)
                for rule in rules:
                    if seen is not None and rule.get("managed") not in seen:
                        continue
                    key = (f"route:{rule['hostname']}" if not rule.get("managed")
                           else rule.get("managed"))
                    data.append(dict(rule, probes=dash.monitor.probes_for(key or "")))
                return self._json({"rules": data})

            match = re.fullmatch(r"/api/jobs/(j\d+)(/stream)?", path)
            if match:
                job = dash.jobs.get(match.group(1))
                if job is None:
                    return self._error(404, "no such job")
                if job.app is not None and not team.allows(self.user, "view", job.app):
                    return self._error(404, "no such job")
                if not team.allows(self.user, "secrets", job.app):
                    data = job.as_dict(with_lines=True)
                    data["lines"] = [re.sub(r"(password[^:]*:\s*)\S+", r"\1••••••", ln, flags=re.I)
                                     for ln in data["lines"]]
                    return self._json(data)
                if not match.group(2):
                    return self._json(job.as_dict(with_lines=True))
                return self._stream_job(job)

            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)(/[a-z/]+(?:/[0-9]{8}-[0-9]{6}\.[a-z.]+)?)?", path)
            if match:
                name, rest = match.group(1), match.group(2) or ""
                if not team.allows(self.user, "view", name):
                    return self._error(404, "no such app")
                if rest == "":
                    detail = dash.app_detail(name)
                    if detail and not team.allows(self.user, "secrets", name):
                        detail = redact(detail)
                    return self._json(detail) if detail else self._error(404, "no such app")
                app = dash.app(name)
                if app is None:
                    return self._error(404, "no such app")
                if rest == "/terminal":
                    return self._terminal(name, app)
                if rest == "/backups":
                    if app.get("kind") not in ("db", "compose"):
                        return self._error(400, "only databases and Docker stacks have backups")
                    holder = app.get("db") or app.get("backup") or {}
                    return self._json({"backups": containers.list_backups(name),
                                       "daily": bool(holder.get("daily_backup")),
                                       "offsite": holder.get("offsite")})
                match_dl = re.fullmatch(r"/backups/([0-9]{8}-[0-9]{6}\.[a-z.]+)", rest)
                if match_dl and not team.allows(self.user, "secrets", name):
                    return self._error(403, "downloading a backup needs the 'secrets' permission")
                if match_dl and app.get("kind") in ("db", "compose"):
                    path = containers.backup_dir(name) / match_dl.group(1)
                    if not path.is_file():
                        return self._error(404, "no such backup")
                    team.audit(self.user or "?", "download backup", name, path.name)
                    data = path.read_bytes()
                    return self._send(200, data, "application/octet-stream",
                                      {"Content-Disposition": f'attachment; filename="{name}-{path.name}"'})
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

        def _proxy(self, method: str, sid: str, rest: str, query: str) -> None:
            """Forward an API call to a linked machine, streams included."""
            if self.user != team.OWNER:
                return self._error(403, "only the owner can manage other machines")
            srv = fleet.servers().get(sid)
            if srv is None:
                return self._error(404, "no such machine")
            if rest.endswith("/terminal"):
                return self._error(501, "open the terminal on that machine's own dashboard")
            body = None
            if method == "POST":
                body = self._raw or b"{}"
                self._audit("remote " + rest.rsplit("/", 1)[-1], f"{sid}:{rest}")
            stream = rest.endswith("/stream")
            try:
                resp = fleet.request(srv, method, rest + (f"?{query}" if query else ""), body, stream=stream)
            except fleet.urllib.error.HTTPError as exc:
                data = exc.read()
                return self._send(exc.code, data, exc.headers.get("Content-Type", "application/json"))
            except Exception as exc:  # noqa: BLE001
                return self._error(502, f"{srv['name']} did not answer: {getattr(exc, 'reason', exc)}")
            with resp:
                ctype = resp.headers.get("Content-Type", "application/json")
                if not stream:
                    return self._send(resp.status, resp.read(), ctype,
                                      {k: v for k, v in resp.headers.items() if k == "Content-Disposition"})
                self._sse_start()
                try:
                    while True:
                        chunk = resp.readline()
                        if not chunk:
                            return
                        self.wfile.write(chunk)
                        if chunk in (b"\n", b"\r\n"):
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        def _terminal(self, name: str, app: dict) -> None:
            if self.headers.get("Upgrade", "").lower() != "websocket":
                return self._error(426, "this endpoint speaks WebSocket")
            if not team.allows(self.user, "terminal", name):
                return self._error(403, "your access does not include the terminal")
            # Cross-site WebSocket hijacking: browsers send cookies with any
            # WebSocket, so the page that opens it must be this dashboard.
            origin = urllib.parse.urlsplit(self.headers.get("Origin", "")).netloc
            if not origin or origin != self.headers.get("Host", ""):
                return self._error(403, "terminal must be opened from this dashboard")
            if app.get("adopted"):
                return self._error(409, "hoist does not know where this process runs")
            key = self.headers.get("Sec-WebSocket-Key", "")
            if not key:
                return self._error(400, "missing Sec-WebSocket-Key")
            argv, env, cwd = terminal.shell_command(name, app)
            team.audit(self.user, "open terminal", name)
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", terminal.accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            terminal.bridge(self.rfile, self.wfile, argv, env, cwd)

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

        def _webhook(self, name: str) -> None:
            """A push from GitHub or GitLab: verify it, then deploy right away.

            No cookie here -- the forge cannot have one. The request must be
            signed with the app's own secret (GitHub's X-Hub-Signature-256,
            or GitLab's X-Gitlab-Token), compared in constant time.
            """
            raw = self._raw
            app = dash.app(name)
            secret = ((app or {}).get("repo") or {}).get("webhook_secret")
            if not secret:
                time.sleep(0.5)
                return self._error(404, "no webhook for this app")
            signature = self.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            gitlab = self.headers.get("X-Gitlab-Token", "")
            if not (hmac.compare_digest(signature, expected) or (gitlab and hmac.compare_digest(gitlab, secret))):
                time.sleep(0.5)
                return self._error(401, "bad signature")
            event = self.headers.get("X-GitHub-Event") or self.headers.get("X-Gitlab-Event") or ""
            if event in ("ping",):
                return self._json({"ok": True, "pong": True})
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._error(400, "payload is not JSON")
            ref = str(payload.get("ref", ""))
            branch = app["repo"]["branch"]
            previews = bool(app["repo"].get("previews"))
            if ref != f"refs/heads/{branch}" and not previews:
                return self._json({"ok": True, "ignored": f"push to {ref}, watching {branch}"})
            team.audit("webhook", "push received", name, ref)
            job = dash.jobs.start(f"Deploy {name} (webhook)", ["sync", name, "--wait"], name, "webhook")
            return self._json({"ok": True, "job": job.id}, 202)

        def _audit(self, action: str, target: str | None = None, detail: str = "") -> None:
            team.audit(self.user or "?", action, target, detail)

        def _api_post(self, path: str, body: dict) -> None:
            if path == "/api/apps":
                self._audit("create app", str(body.get("name") or ""), str(body.get("target", ""))[:200])
                return self._create(body)
            if path == "/api/templates":
                tid = str(body.get("id", ""))
                catalogue = containers.catalogue()
                if tid not in catalogue:
                    raise ValueError("pick a template")
                name = str(body.get("name", "") or tid).strip().lower()
                if not _NAME.match(name):
                    raise ValueError("name: lowercase letters, digits and dashes, up to 40")
                if dash.app(name):
                    raise ValueError(f"{name} already exists")
                args = ["template", tid, "--name", name, "--no-qr"] + (["--local"] if body.get("local") else [])
                self._audit("create from template", name, tid)
                job = dash.jobs.start(f"Start {catalogue[tid]['label']} as {name}", args, name, self.user)
                return self._json({"job": job.as_dict(), "app": name}, 202)
            if path == "/api/servers":
                if self.user != team.OWNER:
                    return self._error(403, "only the owner can link machines")
                try:
                    srv = fleet.add(str(body.get("name", "")), str(body.get("url", "")), str(body.get("token", "")))
                except fleet.FleetError as exc:
                    raise ValueError(str(exc)) from exc
                self._audit("link machine", srv["id"], srv["url"])
                return self._json(srv)
            match = re.fullmatch(r"/api/servers/([a-z0-9-]+)/remove", path)
            if match:
                if self.user != team.OWNER:
                    return self._error(403, "only the owner can unlink machines")
                if not fleet.remove(match.group(1)):
                    return self._error(404, "no such machine")
                self._audit("unlink machine", match.group(1))
                return self._json({"ok": True})
            if path == "/api/databases":
                engine = str(body.get("engine", ""))
                if engine not in containers.ENGINES:
                    raise ValueError("pick postgres, mysql, redis or mongo")
                name = str(body.get("name", "") or engine).strip().lower()
                if not _NAME.match(name):
                    raise ValueError("name: lowercase letters, digits and dashes, up to 40")
                if dash.app(name):
                    raise ValueError(f"{name} already exists")
                self._audit("create database", name, engine)
                job = dash.jobs.start(f"Start {containers.ENGINES[engine]['label']} {name}",
                                      ["db", engine, "--name", name], name, self.user)
                return self._json({"job": job.as_dict(), "app": name}, 202)
            if path == "/api/team":
                if self.user != team.OWNER:
                    return self._error(403, "only the owner can add people")
                name = str(body.get("name", ""))
                role = str(body.get("role", "member"))
                can, apps = body.get("can") or [], body.get("apps") or []
                if not isinstance(can, list) or not isinstance(apps, list):
                    raise ValueError("can and apps must be lists")
                stages = body.get("stages") or []
                if not isinstance(stages, list):
                    raise ValueError("stages must be a list")
                token = team.add(name, role, [str(c) for c in can], [str(a) for a in apps],
                                 [str(s) for s in stages])
                self._audit("add teammate", name.strip().lower(), _describe(role, can, apps, stages))
                base = str(body.get("origin") or "").rstrip("/")
                if not re.match(r"^https?://[^/\s]+$", base):
                    base = ""
                return self._json({"name": name.strip().lower(), "link": f"{base}/?token={token}"})
            match = re.fullmatch(r"/api/team/([a-z][a-z0-9_-]{0,23})/role", path)
            if match:
                if self.user != team.OWNER:
                    return self._error(403, "only the owner can change roles")
                role = str(body.get("role", ""))
                can, apps = body.get("can") or [], body.get("apps") or []
                if not isinstance(can, list) or not isinstance(apps, list):
                    raise ValueError("can and apps must be lists")
                stages = body.get("stages") or []
                if not isinstance(stages, list):
                    raise ValueError("stages must be a list")
                team.set_role(match.group(1), role, [str(c) for c in can], [str(a) for a in apps],
                              [str(s) for s in stages])
                self._audit("change access", match.group(1), _describe(role, can, apps, stages))
                return self._json({"ok": True})
            match = re.fullmatch(r"/api/team/([a-z][a-z0-9_-]{0,23})/remove", path)
            if match:
                if self.user != team.OWNER:
                    return self._error(403, "only the owner can remove people")
                if not team.remove(match.group(1)):
                    return self._error(404, "no such teammate")
                self._audit("remove teammate", match.group(1))
                return self._json({"ok": True})
            if path == "/api/notify":
                self._audit("set notifications", None, "webhook on" if body.get("url") else "off")
                url = str(body.get("url") or "").strip()
                if url and (not url.startswith(("https://", "http://", "smtp://", "smtps://")) or len(url) > 2000):
                    raise ValueError("expected a webhook URL (https://…) or email (smtps://user:pass@host:465/?to=…)")
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
            detail = {"env": f"{len(body.get('env') or {})} variables (values not logged)",
                      "rollback": f"to {str(body.get('sha', ''))[:12]}",
                      "limits": f"memory {body.get('memory') or 'none'}, cpu {body.get('cpu') or 'none'}",
                      "link": f"into {body.get('app', '')} as {body.get('var') or 'default'}"}.get(action, "")
            self._audit(action, name, detail)
            running = dash.jobs.busy(name)
            long_actions = {"redeploy", "rollback", "delete", "backup", "restore"}
            if action in long_actions and running:
                return self._json({"error": "another action is still running", "job": running.as_dict()}, 409)

            if action == "redeploy":
                if not app.get("repo"):
                    return self._error(400, "only apps deployed from git can be redeployed")
                job = dash.jobs.start(f"Redeploy {name}", ["sync", name, "--force"], name, self.user)
                return self._json({"job": job.as_dict()}, 202)
            if action == "rollback":
                sha = str(body.get("sha", ""))
                if not _SHA.match(sha):
                    return self._error(400, "a commit SHA is required")
                job = dash.jobs.start(f"Roll back {name} to {sha[:7]}", ["sync", name, "--to", sha], name, self.user)
                return self._json({"job": job.as_dict()}, 202)
            if action == "delete":
                job = dash.jobs.start(f"Take down {name}", ["down", name] + (["--purge"] if body.get("purge") else []), name, self.user)
                return self._json({"job": job.as_dict()}, 202)

            if action in ("restart", "stop", "start", "pause", "resume"):
                return self._quick(name, app, action)
            if action == "env":
                return self._set_env(name, body)
            if action == "link":
                target = str(body.get("app", ""))
                if app.get("kind") != "db":
                    return self._error(400, "only a database can be linked")
                if not _NAME.match(target) or not dash.app(target):
                    raise ValueError("pick an app to link")
                args = ["link", name, target]
                var = str(body.get("var", "") or "").strip()
                if var:
                    if not _ENV_KEY.match(var):
                        raise ValueError(f"{var!r} is not a valid variable name")
                    args += ["--var", var]
                return self._run_quick(args)
            if action == "exec":
                command = str(body.get("command", "")).strip()
                if not command or len(command) > 2000 or "\n" in command:
                    raise ValueError("one command, on one line")
                if running:
                    return self._json({"error": "another action is still running", "job": running.as_dict()}, 409)
                team.audit(self.user, "run command", name, command[:300])
                job = dash.jobs.start(f"Run in {name}", ["exec", name, "--", command], name, self.user)
                return self._json({"job": job.as_dict()}, 202)
            if action in ("backup", "restore", "backup-schedule"):
                if app.get("kind") not in ("db", "compose"):
                    return self._error(400, "only databases and Docker stacks have backups")
                if action == "backup":
                    job = dash.jobs.start(f"Back up {name}", ["backup", name], name, self.user)
                    return self._json({"job": job.as_dict()}, 202)
                if action == "restore":
                    file = str(body.get("file", ""))
                    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}\.[a-z.]+", file):
                        raise ValueError("pick a backup to restore")
                    job = dash.jobs.start(f"Restore {name} from {file}", ["backup", name, "--restore", file],
                                          name, self.user)
                    return self._json({"job": job.as_dict()}, 202)
                return self._run_quick(["backup", name, "--daily" if body.get("on") else "--no-schedule"])
            if action == "webhook":
                if not app.get("repo"):
                    return self._error(400, "only apps deployed from git have webhooks")
                if self.user != team.OWNER:
                    return self._error(403, "only the owner can issue a webhook secret")
                secret = secrets.token_hex(20)
                with config.transaction() as state:
                    state["apps"][name]["repo"]["webhook_secret"] = secret
                base = str(body.get("origin") or "").rstrip("/")
                if not re.match(r"^https?://[^/\s]+$", base):
                    base = ""
                return self._json({"url": f"{base}/hooks/{name}", "secret": secret})
            if action == "stage":
                scoped = (team.access(self.user) or {}).get("stages")
                if scoped is not None:  # moving an app between environments is a governance change
                    return self._error(403, "people limited to some environments cannot change an app's environment")
                stage = str(body.get("stage", ""))
                if stage not in team.STAGES:
                    raise ValueError(f"pick one of {', '.join(team.STAGES)}")
                return self._run_quick(["stage", name, stage])
            if action == "previews":
                return self._run_quick(["previews", name, "on" if body.get("on") else "off"])
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
                env=dict(os.environ, NO_COLOR="1", HOIST_ACTOR=self.user or "",
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
            job = dash.jobs.start(f"Deploy {app_name}", args, app_name, self.user)
            return self._json({"job": job.as_dict(), "app": app_name}, 202)

    return Handler


_URL_SECRET = re.compile(r"(://[^:/@]*:)[^@]+@")


def redact(detail: dict) -> dict:
    """What a viewer may see of an app: everything but secrets."""
    detail = dict(detail)
    detail["env"] = {k: "••••••" for k in (detail.get("env") or {})}
    if detail.get("row"):
        detail["row"] = _redact_row(detail["row"])
    return detail


def _redact_row(row: dict) -> dict:
    row = dict(row)
    if row.get("db"):
        row["db"] = dict(row["db"], url=_URL_SECRET.sub(r"\1••••••@", row["db"].get("url") or ""))
    return row


def redact_state(snap: dict, keep=lambda name: False) -> dict:
    """Hide database passwords from anyone who lacks the 'secrets' permission on that app."""
    return dict(snap, apps=[r if keep(r["name"]) else _redact_row(r) for r in snap.get("apps", [])])


def _required(path: str):
    """(capability, app) a write to `path` needs, or None when the handler checks for itself."""
    if path in ("/api/apps", "/api/templates", "/api/databases"):
        return ("create", None)
    if path in ("/api/notify", "/api/notify/test"):
        return ("config", None)
    match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/([a-z-]+)", path)
    if match:
        name, action = match.groups()
        cap = team.ACTION_CAP.get(action)
        if cap is None:  # exec and anything else unlisted: the owner only
            return ("owner-only", name)
        return (cap, name)
    return None


def _describe(role: str, can: list, apps: list, stages: list | None = None) -> str:
    if role != "custom":
        return role
    where = (f" on {', '.join(sorted(map(str, apps)))}" if apps else " on all apps")
    if stages:
        where += f" in {', '.join(sorted(map(str, stages)))}"
    return f"custom: {', '.join(sorted(map(str, can))) or 'view only'}" + where


def _denied(user: str | None, cap: str, app: str | None) -> str:
    access = team.access(user) or {}
    if cap == "owner-only":
        return "only the owner can do that"
    if cap not in access.get("caps", ()):
        what = team.CAPS.get(cap, cap)
        return f"your access does not include this ({what.lower()})"
    return f"your access is limited to some apps, and {app or 'this action'} is not one of them"


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
