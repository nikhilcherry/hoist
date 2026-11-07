"""Docker-backed apps: one-click databases and Compose stacks.

hoist still runs everything as a systemd user unit; for these the unit's
command is `docker run` or `docker compose up` in the foreground, so stop,
restart, logs, limits and the dashboard work exactly as for any other app.
"""

from __future__ import annotations

import functools
import re
import secrets
import shlex
import shutil
import subprocess
from pathlib import Path


class ContainerError(Exception):
    pass


@functools.lru_cache(maxsize=1)
def docker() -> str:
    """The docker invocation that actually reaches a daemon.

    A machine with Docker Desktop installed but not running has its context
    pointing at a dead socket while the system daemon is fine; fall back to
    the `default` context instead of failing.
    """
    if not shutil.which("docker"):
        raise ContainerError("docker is not installed")
    for candidate in (["docker"], ["docker", "--context", "default"]):
        result = subprocess.run([*candidate, "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return " ".join(candidate)
    raise ContainerError("docker is installed but no daemon answers — start Docker first")


def available() -> bool:
    try:
        docker()
        return True
    except ContainerError:
        return False


def run(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([*shlex.split(docker()), *args], capture_output=True, text=True,
                          timeout=timeout)


# --- databases ------------------------------------------------------------------

ENGINES: dict[str, dict] = {
    "postgres": {
        "label": "PostgreSQL", "image": "postgres:16-alpine", "port": 5432,
        "data": "/var/lib/postgresql/data", "env_var": "DATABASE_URL",
        "env": lambda db, pw: {"POSTGRES_USER": "hoist", "POSTGRES_PASSWORD": pw, "POSTGRES_DB": db},
        "args": [],
        "url": "postgres://hoist:{pw}@127.0.0.1:{port}/{db}",
        "shell": "psql -U hoist {db}",
    },
    "mysql": {
        "label": "MySQL", "image": "mysql:8.4", "port": 3306, "data": "/var/lib/mysql",
        "env_var": "DATABASE_URL",
        "env": lambda db, pw: {"MYSQL_ROOT_PASSWORD": pw, "MYSQL_DATABASE": db,
                               "MYSQL_USER": "hoist", "MYSQL_PASSWORD": pw},
        "args": [],
        "url": "mysql://hoist:{pw}@127.0.0.1:{port}/{db}",
        "shell": "mysql -u hoist -p {db}",
    },
    "mariadb": {
        "label": "MariaDB", "image": "mariadb:11", "port": 3306, "data": "/var/lib/mysql",
        "env_var": "DATABASE_URL",
        "env": lambda db, pw: {"MARIADB_ROOT_PASSWORD": pw, "MARIADB_DATABASE": db,
                               "MARIADB_USER": "hoist", "MARIADB_PASSWORD": pw},
        "args": [],
        "url": "mysql://hoist:{pw}@127.0.0.1:{port}/{db}",
        "shell": "mariadb -u hoist -p {db}",
    },
    "redis": {
        "label": "Redis", "image": "redis:7-alpine", "port": 6379, "data": "/data",
        "env_var": "REDIS_URL",
        "env": lambda db, pw: {},
        "args": ["redis-server", "--appendonly", "yes", "--requirepass", "{pw}"],
        "url": "redis://:{pw}@127.0.0.1:{port}/0",
        "shell": "redis-cli -a <password>",
    },
    "mongo": {
        "label": "MongoDB", "image": "mongo:7", "port": 27017, "data": "/data/db",
        "env_var": "MONGODB_URL",
        "env": lambda db, pw: {"MONGO_INITDB_ROOT_USERNAME": "hoist", "MONGO_INITDB_ROOT_PASSWORD": pw},
        "args": [],
        "url": "mongodb://hoist:{pw}@127.0.0.1:{port}/{db}?authSource=admin",
        "shell": "mongosh -u hoist -p",
    },
}


def container_name(app: str) -> str:
    return f"hoist-{app}"


def volume_name(app: str) -> str:
    return f"hoist-{app}-data"


def database_spec(engine: str, name: str, port: int, image: str | None = None) -> dict:
    """Everything hoist stores about a database app, password included."""
    if engine not in ENGINES:
        raise ContainerError(f"unknown engine {engine!r} — pick one of {', '.join(ENGINES)}")
    meta = ENGINES[engine]
    password = secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    dbname = re.sub(r"[^a-z0-9_]", "_", name)
    return {
        "engine": engine,
        "image": image or meta["image"],
        "inner_port": meta["port"],
        "password": password,
        "database": dbname,
        "volume": volume_name(name),
        "url": meta["url"].format(pw=password, port=port, db=dbname),
        "env_var": meta["env_var"],
    }


def database_command(name: str, db: dict, limits: dict | None = None) -> str:
    meta = ENGINES[db["engine"]]
    parts = [*shlex.split(docker()), "run", "--rm", "--name", container_name(name),
             "-p", f"127.0.0.1:$PORT:{db['inner_port']}",
             "-v", f"{db['volume']}:{meta['data']}"]
    # A systemd limit would only cap the docker client; the container needs its own.
    if limits and limits.get("memory"):
        parts += ["--memory", limits["memory"].lower()]
    if limits and limits.get("cpu"):
        parts += ["--cpus", f"{int(limits['cpu'].rstrip('%')) / 100:g}"]
    for key, value in meta["env"](db["database"], db["password"]).items():
        parts += ["-e", f"{key}={value}"]
    parts.append(db["image"])
    parts += [a.format(pw=db["password"]) for a in meta["args"]]
    # `$PORT` is left for the unit's shell to expand.
    return " ".join(p if p.startswith("127.0.0.1:$PORT") else shlex.quote(p) for p in parts)


def ready(name: str, db: dict) -> bool:
    """Ask the engine itself whether it accepts connections."""
    check = {
        "postgres": ["pg_isready", "-U", "hoist", "-d", db["database"]],
        "mysql": ["mysqladmin", "ping", "-u", "hoist", f"-p{db['password']}", "--silent"],
        "mariadb": ["healthcheck.sh", "--connect"],
        "redis": ["redis-cli", "--no-auth-warning", "-a", db["password"], "ping"],
        "mongo": ["mongosh", "--quiet", "-u", "hoist", "-p", db["password"], "--authenticationDatabase",
                  "admin", "--eval", "db.adminCommand('ping').ok"],
    }[db["engine"]]
    try:
        out = run("exec", container_name(name), *check, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    return out.returncode == 0


def pull(image: str, log=print) -> None:
    present = run("image", "inspect", image, timeout=30)
    if present.returncode == 0:
        log(f"image {image} is already here")
        return
    log(f"pulling {image} (first time only)")
    proc = subprocess.Popen([*shlex.split(docker()), "pull", image], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    last = ""
    for line in proc.stdout:
        line = line.strip()
        if line and ("Pull complete" in line or "Digest" in line or "Status" in line) and line != last:
            log(line)
            last = line
    if proc.wait() != 0:
        raise ContainerError(f"docker pull {image} failed")


def remove_volume(name: str) -> bool:
    return run("volume", "rm", "-f", volume_name(name), timeout=60).returncode == 0


def pre_start(name: str) -> list[str]:
    """Commands systemd runs before starting: clear a container left behind."""
    return [f"-{docker()} rm -f {container_name(name)}"]


# --- compose --------------------------------------------------------------------

COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")


def compose_file(directory: Path) -> Path | None:
    for name in COMPOSE_FILES:
        if (directory / name).is_file():
            return directory / name
    return None


def compose_project(app: str) -> str:
    return f"hoist-{app}"


def compose_uses_port_var(path: Path) -> bool:
    return bool(re.search(r"\$\{?PORT\b", path.read_text()))


def compose_published_port(path: Path) -> int | None:
    """The first host port the stack publishes, from `ports:` entries."""
    text = path.read_text()
    for match in re.finditer(r"""^\s*-\s*["']?(?:[\d.]+:)?(\d{2,5}):\d{2,5}(?:/tcp)?["']?\s*$""",
                             text, re.MULTILINE):
        return int(match.group(1))
    for match in re.finditer(r"^\s*published:\s*[\"']?(\d{2,5})", text, re.MULTILINE):
        return int(match.group(1))
    return None


def _file_flag(generated: bool) -> str:
    return f" -f {GENERATED_COMPOSE}" if generated else ""


def compose_up_command(app: str, generated: bool = False) -> str:
    return f"{docker()} compose -p {compose_project(app)}{_file_flag(generated)} up --remove-orphans"


def compose_build_step(app: str, generated: bool = False) -> str:
    return f"{docker()} compose -p {compose_project(app)}{_file_flag(generated)} build"


def dockerfile_port(directory: Path) -> int:
    """The port a Dockerfile says it listens on (EXPOSE), else the common 8080."""
    text = (directory / "Dockerfile").read_text()
    match = re.search(r"^\s*EXPOSE\s+(\d+)", text, re.MULTILINE | re.IGNORECASE)
    return int(match.group(1)) if match else 8080


GENERATED_COMPOSE = "compose.hoist.yml"


def ensure_dockerfile_compose(directory: Path) -> Path:
    """Wrap a lone Dockerfile in a one-service compose file hoist can run."""
    inner = dockerfile_port(directory)
    path = directory / GENERATED_COMPOSE
    path.write_text("# written by hoist for a repo with only a Dockerfile\n"
                    "services:\n  app:\n    build: .\n"
                    f'    ports: ["127.0.0.1:${{PORT}}:{inner}"]\n'
                    f'    environment:\n      PORT: "{inner}"\n')
    return path


def compose_down(app: str, purge: bool = False) -> list[str]:
    """Stop and remove a stack. Its volumes are kept unless `purge`; returns the kept ones."""
    subprocess.run([*shlex.split(docker()), "compose", "-p", compose_project(app), "down",
                    "--remove-orphans", *(["--volumes"] if purge else [])],
                   capture_output=True, text=True, timeout=180)
    if purge:
        return []
    listing = run("volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={compose_project(app)}",
                  timeout=30)
    return listing.stdout.split()


# --- backups --------------------------------------------------------------------

BACKUP_KEEP = 10
BACKUP_EXT = {"postgres": "dump", "mysql": "sql", "mariadb": "sql", "redis": "rdb", "mongo": "archive.gz"}


def backup_dir(app: str) -> Path:
    from . import config
    return config.STATE_DIR / "backups" / app


def list_backups(app: str) -> list[dict]:
    root = backup_dir(app)
    if not root.is_dir():
        return []
    files = sorted((p for p in root.iterdir() if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: p.name, reverse=True)
    return [{"file": p.name, "size": p.stat().st_size, "at": p.stat().st_mtime} for p in files]


def _exec(name: str, *cmd: str, env: dict | None = None) -> list[str]:
    args = [*shlex.split(docker()), "exec", "-i"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    return [*args, container_name(name), *cmd]


def backup(name: str, db: dict) -> Path:
    """Dump a running database to a private file. Returns its path."""
    import os
    import time as _time
    engine = db["engine"]
    root = backup_dir(name)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    dest = root / f"{_time.strftime('%Y%m%d-%H%M%S')}.{BACKUP_EXT[engine]}"
    tmp = dest.with_name("." + dest.name + ".partial")
    if engine == "redis":
        # SAVE writes a consistent snapshot inside the container; copy it out.
        out = subprocess.run(_exec(name, "redis-cli", "--no-auth-warning", "-a", db["password"], "SAVE"),
                             capture_output=True, text=True, timeout=600)
        if out.returncode != 0 or "OK" not in out.stdout:
            raise ContainerError(f"redis SAVE failed: {(out.stderr or out.stdout).strip()[:200]}")
        copied = run("cp", f"{container_name(name)}:/data/dump.rdb", str(tmp), timeout=600)
        if copied.returncode != 0:
            raise ContainerError(f"could not copy the snapshot: {copied.stderr.strip()[:200]}")
    else:
        cmd = {
            "postgres": _exec(name, "pg_dump", "-U", "hoist", "-Fc", db["database"]),
            "mysql": _exec(name, "mysqldump", "-u", "hoist", "--single-transaction", "--routines",
                           db["database"], env={"MYSQL_PWD": db["password"]}),
            "mariadb": _exec(name, "mariadb-dump", "-u", "hoist", "--single-transaction", "--routines",
                             db["database"], env={"MYSQL_PWD": db["password"]}),
            "mongo": _exec(name, "mongodump", "--archive", "--gzip", "-u", "hoist", "-p", db["password"],
                           "--authenticationDatabase", "admin", "--quiet"),
        }[engine]
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, timeout=3600)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise ContainerError(f"{engine} dump failed: {proc.stderr.decode(errors='replace').strip()[:300]}")
    os.chmod(tmp, 0o600)
    if tmp.stat().st_size == 0:
        tmp.unlink()
        raise ContainerError("the dump came out empty")
    os.replace(tmp, dest)
    if db.get("offsite"):
        copy_offsite(dest, db["offsite"], name)
    for old in list_backups(name)[BACKUP_KEEP:]:
        (root / old["file"]).unlink(missing_ok=True)
    return dest


def copy_offsite(file: Path, target: str, name: str) -> None:
    """Copy a backup off this machine: an rclone remote (`gdrive:hoist`) or a directory."""
    if ":" in target and not target.startswith("/"):
        if not shutil.which("rclone"):
            raise ContainerError("rclone is not installed, so the off-site copy was skipped")
        out = subprocess.run(["rclone", "copy", str(file), f"{target.rstrip('/')}/{name}"],
                             capture_output=True, text=True, timeout=1800)
        if out.returncode != 0:
            raise ContainerError(f"off-site copy failed: {out.stderr.strip()[-200:]}")
        return
    dest = Path(target).expanduser() / name
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file, dest / file.name)


def restore(name: str, db: dict, file: Path, systemctl=None) -> None:
    """Load a backup into the running database (redis: replaces the data, restarts)."""
    engine = db["engine"]
    if engine == "redis":
        if systemctl is None:
            raise ContainerError("restoring redis needs to restart it")
        systemctl("stop", f"hoist-{name}.service")
        meta = ENGINES["redis"]
        out = subprocess.run([*shlex.split(docker()), "run", "--rm", "-v", f"{db['volume']}:{meta['data']}",
                              "-v", f"{file.parent}:/backup:ro", db["image"], "sh", "-c",
                              # With appendonly on, Redis starts from the AOF and ignores
                              # dump.rdb. So load the snapshot with AOF off, have Redis
                              # rewrite the AOF from it, and shut down cleanly.
                              f"rm -rf /data/appendonlydir && cp /backup/{shlex.quote(file.name)} /data/dump.rdb"
                              " && redis-server --appendonly no --dir /data --daemonize yes"
                              " && until redis-cli ping >/dev/null 2>&1; do sleep 0.2; done"
                              " && redis-cli config set appendonly yes >/dev/null"
                              " && sleep 0.5"
                              " && while redis-cli info persistence | grep -Eq"
                              " 'aof_rewrite_in_progress:1|aof_rewrite_scheduled:1|loading:1'; do sleep 0.2; done"
                              " && redis-cli shutdown"],
                             capture_output=True, text=True, timeout=600)
        systemctl("start", f"hoist-{name}.service")
        if out.returncode != 0:
            raise ContainerError(f"redis restore failed: {out.stderr.strip()[:200]}")
        return
    cmd = {
        "postgres": _exec(name, "pg_restore", "-U", "hoist", "-d", db["database"], "--clean", "--if-exists",
                          "--no-owner"),
        "mysql": _exec(name, "mysql", "-u", "hoist", db["database"], env={"MYSQL_PWD": db["password"]}),
        "mariadb": _exec(name, "mariadb", "-u", "hoist", db["database"], env={"MYSQL_PWD": db["password"]}),
        "mongo": _exec(name, "mongorestore", "--archive", "--gzip", "--drop", "-u", "hoist", "-p",
                       db["password"], "--authenticationDatabase", "admin", "--quiet"),
    }[engine]
    with open(file, "rb") as fh:
        proc = subprocess.run(cmd, stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    if proc.returncode != 0:
        raise ContainerError(f"{engine} restore failed: {proc.stderr.decode(errors='replace').strip()[:300]}")


def backup_unit_base(name: str) -> str:
    return f"hoist-{name}--backup"


def render_backup_units(name: str, python: str, pkg_root: str, hoist_home: str | None) -> tuple[str, str]:
    env = f"Environment=PYTHONPATH={pkg_root}\n" + (f"Environment=HOIST_HOME={hoist_home}\n" if hoist_home else "")
    service = (f"[Unit]\nDescription=hoist: back up {name}\n\n[Service]\nType=oneshot\n{env}"
               f"ExecStart={python} -m hoist backup {name}\n")
    timer = (f"[Unit]\nDescription=hoist: back up {name} every day\n\n[Timer]\nOnCalendar=daily\n"
             f"RandomizedDelaySec=900\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


# --- one-click templates ----------------------------------------------------------

TEMPLATES: dict[str, dict] = {
    "uptime-kuma": {"label": "Uptime Kuma", "about": "Status page and uptime monitors for your demos",
                    "image": "louislam/uptime-kuma:1", "port": 3001, "data": "/app/data"},
    "n8n": {"label": "n8n", "about": "Workflow automation: webhooks, APIs, AI agents",
            "image": "n8nio/n8n:latest", "port": 5678, "data": "/home/node/.n8n",
            "env": {"N8N_SECURE_COOKIE": "false", "N8N_DIAGNOSTICS_ENABLED": "false"}},
    "ntfy": {"label": "ntfy", "about": "Push notifications to any phone with one HTTP call",
             "image": "binwiederhier/ntfy:latest", "port": 80, "data": "/var/cache/ntfy",
             "command": ["serve", "--cache-file", "/var/cache/ntfy/cache.db"]},
    "excalidraw": {"label": "Excalidraw", "about": "Whiteboard for architecture sketches and pitch diagrams",
                   "image": "excalidraw/excalidraw:latest", "port": 80},
    "pocketbase": {"label": "PocketBase", "about": "A backend in one file: auth, database, realtime, admin UI",
                   "image": "ghcr.io/muchobien/pocketbase:latest", "port": 8090, "data": "/pb_data"},
    "metabase": {"label": "Metabase", "about": "Dashboards and charts from your databases",
                 "image": "metabase/metabase:latest", "port": 3000, "data": "/metabase-data",
                 "env": {"MB_DB_FILE": "/metabase-data/metabase.db"}},
    "gitea": {"label": "Gitea", "about": "Your own GitHub: repos, issues, pull requests",
              "image": "gitea/gitea:1.22", "port": 3000, "data": "/data",
              "env": {"USER_UID": "1000", "USER_GID": "1000"}},
    "code-server": {"label": "code-server", "about": "VS Code in the browser for pair programming",
                    "image": "codercom/code-server:latest", "port": 8080, "data": "/home/coder",
                    "env": {"PASSWORD": "{password}"}},
    "minio": {"label": "MinIO", "about": "S3-compatible file storage with a web console",
              "image": "minio/minio:latest", "port": 9001, "data": "/data",
              "command": ["server", "/data", "--console-address", ":9001"],
              "env": {"MINIO_ROOT_USER": "hoist", "MINIO_ROOT_PASSWORD": "{password}"}},
    "mailpit": {"label": "Mailpit", "about": "Catch and read the emails your app sends while you build it",
                "image": "axllent/mailpit:latest", "port": 8025},
    "it-tools": {"label": "IT Tools", "about": "Converters, encoders and generators developers keep googling",
                 "image": "corentinth/it-tools:latest", "port": 80},
    "stirling-pdf": {"label": "Stirling PDF", "about": "Merge, split, OCR and convert PDFs",
                     "image": "stirlingtools/stirling-pdf:latest", "port": 8080},
    "node-red": {"label": "Node-RED", "about": "Wire IoT devices and APIs together visually",
                 "image": "nodered/node-red:latest", "port": 1880, "data": "/data"},
    "grafana": {"label": "Grafana", "about": "Dashboards for metrics and sensor data",
                "image": "grafana/grafana:latest", "port": 3000, "data": "/var/lib/grafana",
                "env": {"GF_SECURITY_ADMIN_PASSWORD": "{password}"}},
    "filebrowser": {"label": "File Browser", "about": "Share and manage files from a web page",
                    "image": "filebrowser/filebrowser:latest", "port": 80, "data": "/srv"},
    "changedetection": {"label": "changedetection.io", "about": "Get told when a web page changes",
                        "image": "ghcr.io/dgtlmoon/changedetection.io:latest", "port": 5000, "data": "/datastore"},
    "jupyter": {"label": "Jupyter", "about": "Notebooks for the data and ML part of the project",
                "image": "quay.io/jupyter/minimal-notebook:latest", "port": 8888, "data": "/home/jovyan/work",
                "env": {"JUPYTER_TOKEN": "{password}"}},
    "memos": {"label": "Memos", "about": "Team notes and a shared scratchpad",
              "image": "neosmemo/memos:stable", "port": 5230, "data": "/var/opt/memos"},
    "nocodb": {"label": "NocoDB", "about": "A spreadsheet-style database with a REST API",
               "image": "nocodb/nocodb:latest", "port": 8080, "data": "/usr/app/data"},
    "directus": {"label": "Directus", "about": "Headless CMS and admin panel with an API",
                 "image": "directus/directus:11", "port": 8055, "data": "/directus/database",
                 "env": {"KEY": "{random}", "SECRET": "{random}", "ADMIN_EMAIL": "admin@example.com",
                         "ADMIN_PASSWORD": "{password}", "DB_CLIENT": "sqlite3",
                         "DB_FILENAME": "/directus/database/data.db"}},
    "linkding": {"label": "linkding", "about": "Shared bookmarks for the team's research",
                 "image": "sissbruecker/linkding:latest", "port": 9090, "data": "/etc/linkding/data"},
    "searxng": {"label": "SearXNG", "about": "Private metasearch your app can call",
                "image": "searxng/searxng:latest", "port": 8080, "data": "/etc/searxng"},
    "ghost": {"label": "Ghost", "about": "A blog or landing page with an editor",
              "image": "ghost:5-alpine", "port": 2368, "data": "/var/lib/ghost/content",
              "env": {"NODE_ENV": "development", "database__client": "sqlite3",
                      "database__connection__filename": "/var/lib/ghost/content/data/ghost.db"}},
    "open-webui": {"label": "Open WebUI", "about": "ChatGPT-style chat for the Ollama models on this machine",
                   "image": "ghcr.io/open-webui/open-webui:main", "port": 8080, "data": "/app/backend/data",
                   "env": {"OLLAMA_BASE_URL": "http://host.docker.internal:11434"}, "host_gateway": True},
}


def template_secrets(template_id: str) -> dict[str, str]:
    """Fresh values for a template's placeholders: {password} is shown to the
    user once; each {random} is its own secret nobody needs to know."""
    return {"password": secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")}


def template_compose(template_id: str, values: dict[str, str] | None = None) -> str:
    """A compose file for a template: one service, its port on ${PORT}, a named volume."""
    spec = TEMPLATES[template_id]
    values = values or template_secrets(template_id)

    def fill(value: str) -> str:
        value = value.replace("{password}", values["password"])
        while "{random}" in value:
            value = value.replace("{random}", secrets.token_hex(24), 1)
        return value
    lines = ["services:", "  app:", f"    image: {spec['image']}",
             f'    ports: ["127.0.0.1:${{PORT}}:{spec["port"]}"]']
    if spec.get("data"):
        lines.append(f'    volumes: ["data:{spec["data"]}"]')
    if spec.get("env"):
        lines.append("    environment:")
        lines += [f'      {k}: "{fill(v)}"' for k, v in spec["env"].items()]
    if spec.get("command"):
        lines.append("    command: [" + ", ".join(f'"{c}"' for c in spec["command"]) + "]")
    if spec.get("host_gateway"):
        lines.append('    extra_hosts: ["host.docker.internal:host-gateway"]')
    if spec.get("data"):
        lines += ["volumes:", "  data: {}"]
    return "\n".join(lines) + "\n"


def template_dir(app: str) -> Path:
    from . import config
    return config.STATE_DIR / "templates" / app


# --- volume backups for stacks --------------------------------------------------

TAR_IMAGE = "busybox:1.36"


def stack_volumes(app: str) -> list[str]:
    out = run("volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={compose_project(app)}",
              timeout=30)
    return sorted(out.stdout.split())


def backup_volumes(app: str, log=print) -> Path:
    """Pause the stack, tar every volume it owns into one archive, unpause."""
    import os
    import time as _time
    volumes = stack_volumes(app)
    if not volumes:
        raise ContainerError(f"{app} has no Docker volumes, so there is nothing to back up")
    pull(TAR_IMAGE, log=lambda _m: None)
    root = backup_dir(app)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    dest = root / f"{_time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    partial = root / f".{dest.name}.partial"
    mounts = [arg for v in volumes for arg in ("-v", f"{v}:/v/{v}:ro")]
    base = [*shlex.split(docker()), "compose", "-p", compose_project(app)]
    subprocess.run([*base, "pause"], capture_output=True, text=True, timeout=60)
    try:
        log(f"archiving {len(volumes)} volume(s): {', '.join(volumes)}")
        # The archive streams out on stdout into a file this user creates, so it
        # is never owned by the container's root.
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            out = subprocess.run([*shlex.split(docker()), "run", "--rm", *mounts, TAR_IMAGE,
                                  "tar", "czf", "-", "-C", "/v", "."],
                                 stdout=fh, stderr=subprocess.PIPE, timeout=3600)
    finally:
        subprocess.run([*base, "unpause"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise ContainerError(f"volume backup failed: {out.stderr.decode(errors='replace').strip()[:300]}")
    os.replace(partial, dest)
    for old in list_backups(app)[BACKUP_KEEP:]:
        (root / old["file"]).unlink(missing_ok=True)
    return dest


def restore_volumes(app: str, file: Path, systemctl) -> None:
    volumes = stack_volumes(app)
    if not volumes:
        raise ContainerError(f"{app} has no volumes to restore into")
    systemctl("stop", f"hoist-{app}.service")
    subprocess.run([*shlex.split(docker()), "compose", "-p", compose_project(app), "stop"],
                   capture_output=True, text=True, timeout=120)
    mounts = [arg for v in volumes for arg in ("-v", f"{v}:/v/{v}")]
    clear = " && ".join(f"find /v/{shlex.quote(v)} -mindepth 1 -delete" for v in volumes)
    with open(file, "rb") as fh:
        out = subprocess.run([*shlex.split(docker()), "run", "--rm", "-i", *mounts, TAR_IMAGE,
                              "sh", "-c", f"{clear} && tar xzf - -C /v"],
                             stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    systemctl("start", f"hoist-{app}.service")
    if out.returncode != 0:
        raise ContainerError(f"volume restore failed: {out.stderr.decode(errors='replace').strip()[:300]}")
