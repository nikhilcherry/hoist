"""The dashboard server: auth, CSRF, static file safety, input validation."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from hoist import config
from hoist.dash import collect, server


class _StubMonitor:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def nudge(self):
        pass

    def get(self):
        return {"apps": [], "system": {}, "routes": {"rules": []}}

    def series(self, name=None):
        return []

    def probes_for(self, key):
        return []


@pytest.fixture
def dash(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "apps.json")
    monkeypatch.setattr(collect, "Monitor", _StubMonitor)
    config.save({"version": 1, "domain": None, "apps": {
        "web": {"name": "web", "port": 9, "workdir": str(tmp_path), "cmd": "true", "env": {}},
    }})
    token = server.load_token()
    httpd = server.serve("127.0.0.1", 0, token)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], token
    httpd.shutdown()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, dict(resp.getheaders()), raw


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_file_is_private(dash, tmp_path):
    assert (tmp_path / "dash-token").stat().st_mode & 0o077 == 0


def test_api_needs_the_token(dash):
    port, token = dash
    assert call(port, "GET", "/api/state")[0] == 401
    assert call(port, "GET", "/api/state", headers=auth("wrong"))[0] == 401
    assert call(port, "GET", "/api/state", headers=auth(token))[0] == 200


def test_signed_out_visitors_get_the_login_page(dash):
    port, _ = dash
    status, _, body = call(port, "GET", "/apps/web")
    assert status == 200 and b"Sign in to this machine" in body


def test_token_link_sets_an_httponly_strict_cookie_and_redirects(dash):
    port, token = dash
    status, headers, _ = call(port, "GET", f"/?token={token}&next=/apps/web")
    assert status == 303 and headers["Location"] == "/apps/web"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_next_cannot_redirect_off_site(dash):
    port, token = dash
    _, headers, _ = call(port, "GET", f"/?token={token}&next=//evil.example")
    assert headers["Location"] == "/"


def test_writes_need_the_csrf_header(dash):
    port, token = dash
    status, _, _ = call(port, "POST", "/api/apps/web/env", {"env": {}}, headers=auth(token))
    assert status == 403


def test_static_files_cannot_escape_their_folder(dash):
    port, _ = dash
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/fonts/../../server.py"):
        status, _, body = call(port, "GET", path)
        assert b"def make_handler" not in body, path


@pytest.mark.parametrize("env,message", [
    ({"BAD-NAME": "x"}, "not a valid variable name"),
    ({"PORT": "1"}, "set by hoist"),
    ({"A": "two\nlines"}, "line break"),
])
def test_env_is_validated(dash, env, message):
    port, token = dash
    status, _, body = call(port, "POST", "/api/apps/web/env", {"env": env},
                           headers={**auth(token), "X-Hoist": "1"})
    assert status == 400 and message in json.loads(body)["error"]


def test_rollback_requires_a_commit_sha(dash):
    port, token = dash
    status, _, _ = call(port, "POST", "/api/apps/web/rollback", {"sha": "; rm -rf /"},
                        headers={**auth(token), "X-Hoist": "1"})
    assert status == 400


def test_new_app_rejects_a_bad_name(dash):
    port, token = dash
    status, _, body = call(port, "POST", "/api/apps", {"target": "github.com/a/b", "name": "../../x"},
                           headers={**auth(token), "X-Hoist": "1"})
    assert status == 400


def test_qr_endpoint_returns_svg(dash):
    port, token = dash
    status, headers, body = call(port, "GET", "/api/qr.svg?text=https://x.example", headers=auth(token))
    assert status == 200 and headers["Content-Type"] == "image/svg+xml" and body.startswith(b"<svg")


def test_a_member_can_act_but_cannot_manage_people(dash):
    from hoist.dash import team
    port, _ = dash
    member = team.add("shreyas")
    status, _, body = call(port, "GET", "/api/state", headers=auth(member))
    assert status == 200 and json.loads(body)["me"] == "shreyas"
    status, _, _ = call(port, "POST", "/api/team", {"name": "intruder"},
                        headers={**auth(member), "X-Hoist": "1"})
    assert status == 403
    call(port, "POST", "/api/apps/web/env", {"env": {"A": "1"}, "restart": False},
         headers={**auth(member), "X-Hoist": "1"})
    assert team.audit_tail()[0]["user"] == "shreyas"


def _hooked(monkeypatch, branch="main"):
    from hoist.dash import jobs
    started = []

    class _Job:
        id = "j1"
        def as_dict(self):
            return {"id": "j1"}

    monkeypatch.setattr(jobs.Jobs, "start", lambda self, title, args, app=None, actor=None: started.append(args) or _Job())
    monkeypatch.setattr(jobs.Jobs, "busy", lambda self, app: None)
    with config.transaction() as st:
        st["apps"]["web"]["repo"] = {"url": "u", "branch": branch, "webhook_secret": "s3cret"}
    return started


def _signed(body: bytes, secret="s3cret"):
    import hashlib, hmac
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_signed_push_webhook_deploys(dash, monkeypatch):
    port, _ = dash
    started = _hooked(monkeypatch)
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/hooks/web", body=body, headers={"X-Hub-Signature-256": _signed(body),
                                                           "X-GitHub-Event": "push", "Content-Type": "application/json"})
    resp = conn.getresponse(); resp.read()
    assert resp.status == 202 and started == [["sync", "web", "--wait"]]


def test_a_webhook_with_a_bad_signature_is_refused(dash, monkeypatch):
    port, _ = dash
    started = _hooked(monkeypatch)
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/hooks/web", body=body, headers={"X-Hub-Signature-256": _signed(body, "wrong")})
    resp = conn.getresponse(); resp.read()
    assert resp.status == 401 and started == []


def test_gitlab_token_and_other_branches(dash, monkeypatch):
    port, _ = dash
    started = _hooked(monkeypatch)
    for ref, expect in (("refs/heads/feature", 0), ("refs/heads/main", 1)):
        body = json.dumps({"ref": ref}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/hooks/web", body=body, headers={"X-Gitlab-Token": "s3cret", "X-Gitlab-Event": "Push Hook"})
        conn.getresponse().read()
        assert len(started) == expect


def test_a_viewer_sees_no_secrets_and_cannot_act(dash):
    from hoist.dash import team
    port, _ = dash
    with config.transaction() as st:
        st["apps"]["web"]["env"] = {"API_KEY": "super-secret"}
    viewer = team.add("judge", "viewer")
    status, _, body = call(port, "GET", "/api/apps/web", headers=auth(viewer))
    assert status == 200 and b"super-secret" not in body
    status, _, _ = call(port, "POST", "/api/apps/web/restart", {}, headers={**auth(viewer), "X-Hoist": "1"})
    assert status == 403


def test_websocket_accept_key_matches_the_rfc_example():
    from hoist.dash import terminal
    assert terminal.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_websocket_frames_round_trip_with_masking():
    import io, struct
    from hoist.dash import terminal
    payload = b"d" + b"x" * 300
    mask = b"\x01\x02\x03\x04"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    raw = struct.pack("!BBH", 0x81, 0x80 | 126, len(payload)) + mask + masked
    assert terminal.read_frame(io.BytesIO(raw)) == (1, payload)
    out = terminal.frame(b"hello")
    assert out[0] == 0x82 and out[1] == 5 and out[2:] == b"hello"


def _ws_headers(token, origin):
    return {**auth(token), "Upgrade": "websocket", "Connection": "Upgrade",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13", "Origin": origin}


def test_terminal_refuses_other_origins_and_members(dash):
    from hoist.dash import team
    port, token = dash
    status, _, _ = call(port, "GET", "/api/apps/web/terminal", headers=_ws_headers(token, "https://evil.example"))
    assert status == 403
    member = team.add("dev")
    status, _, _ = call(port, "GET", "/api/apps/web/terminal", headers=_ws_headers(member, f"http://127.0.0.1:{port}"))
    assert status == 403


def test_a_linked_machine_is_managed_through_the_proxy(dash, monkeypatch, tmp_path):
    """Two dashboards in one process: link the second, read and act through the first."""
    from hoist.dash import fleet, team
    port, token = dash
    remote_state = {"version": 1, "domain": None, "apps": {
        "edge": {"name": "edge", "port": 9, "workdir": "/tmp", "cmd": "true", "env": {"K": "v"}}}}
    other = server.serve("127.0.0.1", 0, "other-token")

    real_load = config.load
    remote_port = other.server_address[1]

    def load_for(*a, **k):  # the second machine has its own state
        if threading.current_thread().name.startswith("remote"):
            return json.loads(json.dumps(remote_state))
        return real_load(*a, **k)

    class RemoteHandler(other.RequestHandlerClass):
        def handle(self):
            threading.current_thread().name = "remote-" + threading.current_thread().name
            super().handle()
    other.RequestHandlerClass = RemoteHandler
    monkeypatch.setattr(config, "load", load_for)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        status, _, body = call(port, "POST", "/api/servers",
                               {"name": "Box Two", "url": f"http://127.0.0.1:{remote_port}", "token": "nope"},
                               headers={**auth(token), "X-Hoist": "1"})
        assert status == 400 and b"refused" in body
        status, _, body = call(port, "POST", "/api/servers",
                               {"name": "Box Two", "url": f"http://127.0.0.1:{remote_port}",
                                "token": f"http://x/?token=other-token"},
                               headers={**auth(token), "X-Hoist": "1"})
        assert status == 200 and json.loads(body)["id"] == "box-two"
        assert (tmp_path / "servers.json").stat().st_mode & 0o077 == 0
        status, _, body = call(port, "GET", "/api/remote/box-two/apps/edge", headers=auth(token))
        assert status == 200 and json.loads(body)["env"] == {"K": "v"}
        member = team.add("dev")
        status, _, _ = call(port, "GET", "/api/remote/box-two/state", headers=auth(member))
        assert status == 403
        assert b"other-token" not in call(port, "GET", "/api/servers", headers=auth(token))[2]
    finally:
        other.shutdown()
