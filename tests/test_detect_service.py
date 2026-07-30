"""Project detection and systemd unit rendering."""

from __future__ import annotations

import json
import socket

import pytest

from hoist import detect, service


# --- slug ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("My App", "my-app"),
        ("weird__name!!", "weird-name"),
        ("--leading-and-trailing--", "leading-and-trailing"),
        ("Already-Fine", "already-fine"),
        ("!!!", "app"),
        ("", "app"),
    ],
)
def test_slug(raw, expected):
    assert detect.slug(raw) == expected


def test_slug_is_dns_safe_and_bounded():
    out = detect.slug("A" * 100)
    assert len(out) <= 40
    assert all(c.isalnum() or c == "-" for c in out)


# --- command detection --------------------------------------------------------


def test_detects_npm_start(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node ."}}))
    cmd, how = detect.detect_command(tmp_path)
    assert cmd == "npm start"
    assert "package.json" in how


def test_prefers_start_over_dev(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite", "start": "node ."}})
    )
    assert detect.detect_command(tmp_path)[0] == "npm start"


def test_falls_back_to_npm_run_dev(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
    assert detect.detect_command(tmp_path)[0] == "npm run dev"


def test_procfile_wins_over_package_json(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node ."}}))
    (tmp_path / "Procfile").write_text("web: gunicorn app:app --port $PORT\n")
    cmd, how = detect.detect_command(tmp_path)
    assert cmd.startswith("gunicorn")
    assert how == "Procfile web process"


def test_detects_python_entrypoint(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')")
    cmd, how = detect.detect_command(tmp_path)
    assert cmd.endswith("app.py")
    assert "Python" in how


def test_detects_django(tmp_path):
    (tmp_path / "manage.py").write_text("")
    cmd, how = detect.detect_command(tmp_path)
    assert "runserver" in cmd and "$PORT" in cmd
    assert how == "Django"


def test_detects_static_site(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    cmd, how = detect.detect_command(tmp_path)
    assert "http.server" in cmd and "$PORT" in cmd
    assert "static" in how


def test_compose_gives_an_actionable_error(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}")
    with pytest.raises(detect.DetectionError, match="adopt"):
        detect.detect_command(tmp_path)


def test_empty_directory_is_an_error(tmp_path):
    with pytest.raises(detect.DetectionError, match="--cmd"):
        detect.detect_command(tmp_path)


# --- ports --------------------------------------------------------------------


def test_free_port_returns_a_usable_port():
    port = detect.free_port()
    assert 1024 < port < 65536
    assert detect.port_is_free(port)


def test_free_port_rejects_a_taken_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        taken = sock.getsockname()[1]
        assert not detect.port_is_free(taken)
        assert detect.port_in_use(taken)
        with pytest.raises(detect.DetectionError):
            detect.free_port(taken)


# --- systemd unit -------------------------------------------------------------


def sample_app(**overrides):
    app = {
        "name": "demo",
        "port": 8123,
        "workdir": "/srv/demo",
        "cmd": "/bin/echo hello $PORT",
        "env": {},
    }
    app.update(overrides)
    return app


def test_unit_name():
    assert service.unit_name("demo") == "hoist-demo.service"


def test_unit_substitutes_port_and_sets_workdir():
    unit = service.render_unit(sample_app())
    assert "WorkingDirectory=/srv/demo" in unit
    assert "Environment=PORT=8123" in unit
    assert "8123" in unit
    assert "$PORT" not in unit


def test_unit_includes_extra_env():
    unit = service.render_unit(sample_app(env={"API_KEY": "xyz", "DEBUG": "1"}))
    assert "Environment=API_KEY=xyz" in unit
    assert "Environment=DEBUG=1" in unit


def test_unit_restarts_on_failure():
    unit = service.render_unit(sample_app())
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_shell_operators_are_wrapped_in_a_shell():
    assert service.build_exec_start("foo | bar > baz").startswith("/bin/sh -lc ")


def test_unknown_binary_defers_to_a_login_shell():
    exec_start = service.build_exec_start("some-tool-not-on-path --flag")
    assert exec_start.startswith("/bin/sh -lc ")


def test_known_binary_is_resolved_to_an_absolute_path():
    exec_start = service.build_exec_start("echo hi")
    assert exec_start.startswith("/")
    assert exec_start.endswith("hi")


def test_empty_command_is_rejected():
    with pytest.raises(service.ServiceError):
        service.build_exec_start("   ")
