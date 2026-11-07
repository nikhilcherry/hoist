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
