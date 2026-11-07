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

    def touch(self):
        pass

    def get(self):
        apps = [{"name": n, "kind": "app"} for n in config.load().get("apps", {})]
        return {"apps": apps, "system": {}, "routes": {"rules": []}}

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


# --- granular permissions ---------------------------------------------------------


@pytest.fixture
def two_apps(dash):
    with config.transaction() as st:
        st["apps"]["api"] = {"name": "api", "port": 8, "workdir": "/tmp", "cmd": "true",
                             "env": {"SECRET": "s-api"}, "repo": {"url": "u", "branch": "main"}}
        st["apps"]["web"]["env"] = {"SECRET": "s-web"}
    return dash


def _w(token):
    return {**auth(token), "X-Hoist": "1"}


def test_a_scoped_person_sees_and_touches_only_their_apps(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    tok = team.add("judge", "custom", ["operate", "deploy"], ["api"])
    state = json.loads(call(port, "GET", "/api/state", headers=auth(tok))[2])
    assert [a["name"] for a in state["apps"]] == ["api"] and state["scope"] == ["api"]
    assert call(port, "GET", "/api/apps/web", headers=auth(tok))[0] == 404
    assert call(port, "GET", "/api/apps/api", headers=auth(tok))[0] == 200
    assert call(port, "POST", "/api/apps/web/restart", {}, headers=_w(tok))[0] == 403
    status, _, body = call(port, "POST", "/api/apps/api/restart", {}, headers=_w(tok))
    assert status != 403  # allowed; the restart itself may fail in the sandbox
    # creating an app is machine-wide, so a scoped person cannot
    assert call(port, "POST", "/api/apps", {"target": "github.com/a/b"}, headers=_w(tok))[0] == 403


def test_capabilities_are_granted_one_by_one(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    tok = team.add("ops", "custom", ["operate"], [])
    assert call(port, "POST", "/api/apps/web/restart", {}, headers=_w(tok))[0] != 403
    for action, body in (("env", {"env": {}}), ("redeploy", {}), ("delete", {}), ("limits", {})):
        status, _, text = call(port, "POST", f"/api/apps/web/{action}", body, headers=_w(tok))
        assert status == 403, action
    assert b"environment variables" in text or b"your access" in text


def test_secrets_are_a_permission_not_a_role(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    blind = team.add("blind", "custom", ["operate"], [])
    sighted = team.add("sighted", "custom", ["operate", "secrets"], [])
    assert b"s-web" not in call(port, "GET", "/api/apps/web", headers=auth(blind))[2]
    assert b"s-web" in call(port, "GET", "/api/apps/web", headers=auth(sighted))[2]


def test_the_audit_log_is_limited_to_a_scoped_persons_apps(two_apps):
    from hoist.dash import team
    port, token = two_apps
    team.audit("owner", "restart", "web")
    team.audit("owner", "restart", "api")
    tok = team.add("judge", "custom", [], ["api"])
    rows = json.loads(call(port, "GET", "/api/team", headers=auth(tok))[2])["audit"]
    assert {r["target"] for r in rows} == {"api"}


def test_the_terminal_is_a_deliberate_grant_and_people_management_is_owner_only(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    everything_but = team.add("almost", "custom", [c for c in team.CAPS if c != "terminal"], [])
    assert call(port, "POST", "/api/apps/web/exec", {"command": "id"}, headers=_w(everything_but))[0] == 403
    assert call(port, "POST", "/api/team", {"name": "x"}, headers=_w(everything_but))[0] == 403
    member = team.add("plain", "member")
    assert call(port, "POST", "/api/apps/web/exec", {"command": "id"}, headers=_w(member))[0] == 403
    shell = team.add("shell", "custom", ["terminal"], ["api"])
    assert call(port, "POST", "/api/apps/web/exec", {"command": "id"}, headers=_w(shell))[0] == 403  # not their app
    assert call(port, "POST", "/api/apps/api/exec", {"command": "id"}, headers=_w(shell))[0] != 403


def test_unknown_permissions_are_refused(two_apps):
    from hoist.dash import team
    with pytest.raises(team.TeamError):
        team.add("x", "custom", ["sudo"], [])


def test_a_rejected_post_does_not_corrupt_the_next_request_on_the_same_connection(two_apps):
    """The bug this pins: an early 403 left the request body unread, so on a
    keep-alive connection it was parsed as the next request's first line and
    the next (allowed) click came back as '501 Unsupported method'."""
    from hoist.dash import team
    port, _ = two_apps
    tok = team.add("ops", "custom", ["operate"], ["api"])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {**auth(tok), "X-Hoist": "1", "Content-Type": "application/json"}
    conn.request("POST", "/api/apps/web/restart", body=b'{"x": 1}', headers=headers)   # not in scope: 403
    first = conn.getresponse(); first.read()
    assert first.status == 403
    conn.request("POST", "/api/apps/api/restart", body=b"{}", headers=headers)          # allowed
    second = conn.getresponse(); second.read()
    assert second.status not in (400, 501), second.status
    conn.close()


# --- environments (stages) ------------------------------------------------------------


def _label(**stages):
    with config.transaction() as st:
        for name, stage in stages.items():
            st["apps"][name]["stage"] = stage


def test_environment_scope_shows_and_allows_only_that_environment(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    _label(web="production", api="staging")
    tok = team.add("qa", "custom", ["operate"], [], ["staging"])
    names = [a["name"] for a in json.loads(call(port, "GET", "/api/state", headers=auth(tok))[2])["apps"]]
    assert names == ["api"]
    assert call(port, "GET", "/api/apps/web", headers=auth(tok))[0] == 404
    assert call(port, "POST", "/api/apps/web/restart", {}, headers=_w(tok))[0] == 403
    assert call(port, "POST", "/api/apps/api/restart", {}, headers=_w(tok))[0] != 403


def test_an_app_moved_into_an_environment_becomes_visible_without_editing_the_person(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    _label(web="production", api="production")
    tok = team.add("qa", "custom", ["operate"], [], ["staging"])
    assert json.loads(call(port, "GET", "/api/state", headers=auth(tok))[2])["apps"] == []
    _label(api="staging")
    names = [a["name"] for a in json.loads(call(port, "GET", "/api/state", headers=auth(tok))[2])["apps"]]
    assert names == ["api"]


def test_unlabelled_apps_are_production_and_previews_are_previews(two_apps):
    from hoist.dash import team
    with config.transaction() as st:
        st["apps"]["web-feature"] = {"name": "web-feature", "port": 3, "preview_of": "web"}
    assert team.stage_of("web") == "production"
    assert team.stage_of("web-feature") == "preview"


def test_stage_and_app_scopes_must_both_match(two_apps):
    from hoist.dash import team
    _label(web="staging", api="staging")
    tok = team.add("narrow", "custom", ["operate"], ["api"], ["staging"])
    assert team.allows(tok and "narrow", "operate", "api") and not team.allows("narrow", "operate", "web")
    _label(api="production")
    assert not team.allows("narrow", "operate", "api")  # right app, wrong environment now


def test_changing_an_apps_environment_needs_the_config_permission(two_apps):
    from hoist.dash import team
    port, _ = two_apps
    no_config = team.add("ops", "custom", ["operate"], [])
    assert call(port, "POST", "/api/apps/web/stage", {"stage": "staging"}, headers=_w(no_config))[0] == 403
    can_config = team.add("cfg", "custom", ["config"], [])
    status, _, body = call(port, "POST", "/api/apps/web/stage", {"stage": "nonsense"}, headers=_w(can_config))
    assert status == 400 and b"pick one of" in body


def test_unknown_environments_are_refused(two_apps):
    from hoist.dash import team
    with pytest.raises(team.TeamError):
        team.add("x", "custom", ["operate"], [], ["prod"])


def test_someone_limited_to_an_environment_cannot_relabel_apps(two_apps):
    """The bug this pins: a staging-scoped person with 'config' could move their
    own app to production, silently taking it out of their own scope."""
    from hoist.dash import team
    port, _ = two_apps
    _label(api="staging")
    scoped = team.add("qa", "custom", ["config"], [], ["staging"])
    status, _, body = call(port, "POST", "/api/apps/api/stage", {"stage": "production"}, headers=_w(scoped))
    assert status == 403 and b"cannot change" in body
    assert team.stage_of("api") == "staging"
    unscoped = team.add("lead", "custom", ["config"], [])
    assert call(port, "POST", "/api/apps/api/stage", {"stage": "staging"}, headers=_w(unscoped))[0] != 403


def test_api_responses_are_not_delayed_by_nagle_and_delayed_ack(dash):
    """The bug this pins: headers and body left as two small writes, so each API
    call in a browser took ~45 ms (Nagle waiting for a delayed ACK). Thirty calls
    on one keep-alive connection must come back in a few ms each."""
    import time
    port, token = dash
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    times = []
    for _ in range(30):
        started = time.perf_counter()
        conn.request("GET", "/api/state", headers=auth(token))
        conn.getresponse().read()
        times.append(time.perf_counter() - started)
    conn.close()
    times.sort()
    assert times[15] < 0.02, f"median {times[15] * 1000:.1f} ms"
    assert server.make_handler(object()).disable_nagle_algorithm is True


def test_the_monitor_samples_slowly_when_nobody_is_watching(monkeypatch):
    """Each sample spawns systemctl and docker; idling at the full rate cost about
    0.7 % of a core. Nobody watching means a slow rate; a request speeds it up."""
    import time
    monkeypatch.setattr(collect.Monitor, "refresh", lambda self: None)
    monitor = collect.Monitor()
    assert monitor.interval() == collect.IDLE_EVERY
    monitor.touch()
    assert monitor.interval() == collect.SAMPLE_EVERY and monitor.wake.is_set()
    monitor.last_watched = time.time() - collect.WATCHING_FOR - 1
    assert monitor.interval() == collect.IDLE_EVERY
