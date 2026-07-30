"""Ingress-editing tests.

These matter more than anything else in hoist: the config being edited is a
live file that usually also contains rules a human wrote by hand.
"""

from __future__ import annotations

import pytest

from hoist import tunnel

BASE = """\
tunnel: 00000000-1111-2222-3333-444444444444
credentials-file: /etc/cloudflared/creds.json

ingress:
  - hostname: n8n.example.com
    service: http://localhost:5678
  - hostname: chat.example.com
    service: http://localhost:8080
  - service: http_status:404
"""

NO_CATCHALL = """\
tunnel: abc123

ingress:
  - hostname: only.example.com
    service: http://localhost:1234
"""


def test_parses_existing_entries():
    _, entries, _ = tunnel.parse(BASE)
    assert [e.hostname for e in entries] == [
        "n8n.example.com",
        "chat.example.com",
        None,
    ]
    assert entries[-1].is_catchall
    assert all(e.managed is None for e in entries)


def test_tunnel_id_and_domain():
    assert tunnel.tunnel_id(BASE) == "00000000-1111-2222-3333-444444444444"
    assert tunnel.default_domain(BASE) == "example.com"
    assert tunnel.hostnames(BASE) == ["n8n.example.com", "chat.example.com"]


def test_upsert_inserts_before_catchall():
    out = tunnel.upsert(BASE, "demo", "demo.example.com", 8123)
    _, entries, _ = tunnel.parse(out)
    assert [e.hostname for e in entries][-2:] == ["demo.example.com", None]
    assert entries[-1].is_catchall
    assert entries[-2].managed == "demo"
    assert "service: http://localhost:8123" in out


def test_upsert_preserves_hand_written_rules_verbatim():
    out = tunnel.upsert(BASE, "demo", "demo.example.com", 8123)
    for line in BASE.splitlines():
        assert line in out.splitlines()


def test_upsert_is_idempotent_and_updates_port():
    once = tunnel.upsert(BASE, "demo", "demo.example.com", 8123)
    twice = tunnel.upsert(once, "demo", "demo.example.com", 9999)
    assert twice.count("# hoist:demo") == 1
    assert "9999" in twice
    assert "8123" not in twice


def test_remove_is_an_exact_inverse():
    out = tunnel.upsert(BASE, "demo", "demo.example.com", 8123)
    restored, changed = tunnel.remove(out, "demo")
    assert changed
    assert restored == BASE


def test_remove_unknown_name_is_a_no_op():
    restored, changed = tunnel.remove(BASE, "nope")
    assert not changed
    assert restored == BASE


def test_refuses_to_clobber_hand_written_hostname():
    with pytest.raises(tunnel.TunnelError, match="hand-written"):
        tunnel.upsert(BASE, "steal", "n8n.example.com", 9000)


def test_refuses_hostname_owned_by_another_hoist_app():
    out = tunnel.upsert(BASE, "first", "shared.example.com", 1111)
    with pytest.raises(tunnel.TunnelError, match="already hoisted"):
        tunnel.upsert(out, "second", "shared.example.com", 2222)


def test_adds_catchall_when_missing():
    out = tunnel.upsert(NO_CATCHALL, "demo", "demo.example.com", 8123)
    _, entries, _ = tunnel.parse(out)
    assert entries[-1].is_catchall
    assert "http_status:404" in out


def test_multiple_apps_keep_catchall_last():
    out = BASE
    for i, name in enumerate(["a", "b", "c"], start=1):
        out = tunnel.upsert(out, name, f"{name}.example.com", 8000 + i)
    _, entries, _ = tunnel.parse(out)
    assert entries[-1].is_catchall
    assert [e.managed for e in entries if e.managed] == ["a", "b", "c"]


def test_removing_one_of_many_leaves_the_rest():
    out = BASE
    for name in ["a", "b"]:
        out = tunnel.upsert(out, name, f"{name}.example.com", 8000)
    out, _ = tunnel.remove(out, "a")
    assert "a.example.com" not in out
    assert "b.example.com" in out
    assert "n8n.example.com" in out


def test_missing_ingress_block_is_an_error():
    with pytest.raises(tunnel.TunnelError, match="ingress"):
        tunnel.parse("tunnel: abc\n")


def test_indentation_is_followed():
    four_space = BASE.replace("\n  ", "\n    ")
    out = tunnel.upsert(four_space, "demo", "demo.example.com", 8123)
    assert "    # hoist:demo" in out
    assert "    - hostname: demo.example.com" in out


def test_validate_passes_config_before_the_subcommand(monkeypatch, tmp_path):
    """`--config` is a flag on `tunnel`, not on `ingress validate`.

    Put it after the subcommand and cloudflared prints "Incorrect Usage" and
    exits 0, which silently turns validation into a no-op.
    """
    seen: dict[str, list[str]] = {}

    class Result:
        returncode = 0
        stdout = "OK"
        stderr = ""

    monkeypatch.setattr(tunnel.shutil, "which", lambda _: "/usr/bin/cloudflared")
    monkeypatch.setattr(
        tunnel.subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and None or Result()
    )
    tunnel.validate(tmp_path / "config.yml")

    cmd = seen["cmd"]
    assert cmd.index("--config") < cmd.index("ingress"), cmd
    assert cmd[:2] == ["cloudflared", "tunnel"]


def test_validate_rejects_a_usage_error(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "Incorrect Usage: flag provided but not defined: -config"
        stderr = ""

    monkeypatch.setattr(tunnel.shutil, "which", lambda _: "/usr/bin/cloudflared")
    monkeypatch.setattr(tunnel.subprocess, "run", lambda cmd, **kw: Result())
    with pytest.raises(tunnel.TunnelError, match="cannot validate"):
        tunnel.validate(tmp_path / "config.yml")
