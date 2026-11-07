"""Databases and Compose stacks: specs, commands and port discovery."""

from __future__ import annotations

import pytest

from hoist import containers, service


@pytest.fixture(autouse=True)
def fake_docker(monkeypatch):
    monkeypatch.setattr(containers, "docker", lambda: "docker")


@pytest.mark.parametrize("engine,scheme,var", [
    ("postgres", "postgres://", "DATABASE_URL"),
    ("mysql", "mysql://", "DATABASE_URL"),
    ("redis", "redis://", "REDIS_URL"),
    ("mongo", "mongodb://", "MONGODB_URL"),
])
def test_database_spec_has_a_url_with_its_own_password(engine, scheme, var):
    a = containers.database_spec(engine, "votes", 5544)
    b = containers.database_spec(engine, "votes", 5544)
    assert a["url"].startswith(scheme) and ":5544" in a["url"]
    assert a["password"] in a["url"] and a["password"] != b["password"]
    assert a["env_var"] == var and len(a["password"]) >= 20


def test_database_binds_to_localhost_only_and_keeps_data_in_a_volume():
    db = containers.database_spec("postgres", "votes", 5544)
    cmd = containers.database_command("votes", db)
    assert "127.0.0.1:$PORT:5432" in cmd
    assert "hoist-votes-data:/var/lib/postgresql/data" in cmd
    unit = service.render_unit({"name": "votes", "port": 5544, "workdir": "/tmp", "cmd": cmd,
                                "kind": "db", "pre": containers.pre_start("votes")})
    assert "127.0.0.1:5544:5432" in unit
    assert "ExecStartPre=-docker rm -f hoist-votes" in unit


def test_unknown_engine_is_refused():
    with pytest.raises(containers.ContainerError):
        containers.database_spec("oracle", "x", 1)


@pytest.mark.parametrize("ports,expected", [
    ('    ports:\n      - "8080:80"\n', 8080),
    ("    ports:\n      - 127.0.0.1:3000:3000\n", 3000),
    ("    ports:\n      - target: 80\n        published: 8081\n", 8081),
    ("    expose:\n      - 80\n", None),
])
def test_compose_published_port(tmp_path, ports, expected):
    path = tmp_path / "compose.yml"
    path.write_text("services:\n  web:\n    image: nginx\n" + ports)
    assert containers.compose_published_port(path) == expected


def test_compose_port_variable_is_noticed(tmp_path):
    path = tmp_path / "compose.yml"
    path.write_text('services:\n  web:\n    ports: ["${PORT}:80"]\n')
    assert containers.compose_uses_port_var(path)


def test_unit_files_are_private(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "UNIT_DIR", tmp_path)
    path = service.write_unit({"name": "s", "port": 1, "workdir": "/tmp", "cmd": "true",
                               "env": {"API_KEY": "secret"}})
    assert path.stat().st_mode & 0o077 == 0


def test_a_lone_dockerfile_is_wrapped_with_its_exposed_port(tmp_path):
    from hoist import detect
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\nEXPOSE 8000\n")
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node ."}}')
    assert detect.detect_command(tmp_path)[0] == detect.DOCKERFILE
    path = containers.ensure_dockerfile_compose(tmp_path)
    text = path.read_text()
    assert "build: ." in text and "127.0.0.1:${PORT}:8000" in text
    assert containers.compose_uses_port_var(path)
    assert "-f compose.hoist.yml" in containers.compose_up_command("web", generated=True)


def test_dockerfile_without_expose_defaults_to_8080(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM nginx\n")
    assert containers.dockerfile_port(tmp_path) == 8080


def test_mariadb_is_a_first_class_engine():
    db = containers.database_spec("mariadb", "shop", 3307)
    assert db["url"].startswith("mysql://") and db["image"].startswith("mariadb:")
    assert "MARIADB_PASSWORD=" in containers.database_command("shop", db)


def test_database_limits_go_to_docker_not_systemd():
    db = containers.database_spec("postgres", "votes", 5544)
    cmd = containers.database_command("votes", db, {"memory": "512M", "cpu": "150%"})
    assert "--memory 512m" in cmd and "--cpus 1.5" in cmd
    unit = service.render_unit({"name": "votes", "port": 5544, "workdir": "/tmp", "cmd": cmd,
                                "kind": "db", "limits": {"memory": "512M"}})
    assert "MemoryMax" not in unit


def test_offsite_copy_to_a_folder(tmp_path):
    src = tmp_path / "20260101-000000.dump"
    src.write_bytes(b"data")
    containers.copy_offsite(src, str(tmp_path / "remote"), "votes")
    assert (tmp_path / "remote" / "votes" / src.name).read_bytes() == b"data"


@pytest.mark.parametrize("tid", sorted(containers.TEMPLATES))
def test_every_template_publishes_its_port_on_localhost(tid):
    text = containers.template_compose(tid)
    assert "127.0.0.1:${PORT}:" in text and containers.TEMPLATES[tid]["image"] in text
