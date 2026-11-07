"""An interactive terminal in the browser: a WebSocket bridged to a PTY.

Standard library only. The WebSocket part is the small subset of RFC 6455 a
terminal needs (text and binary frames, ping, close); the shell runs in a
pseudo-terminal so full-screen programs (top, vim, psql) behave. Client
messages are text frames: "d<data>" is keystrokes, "r<cols>,<rows>" a resize.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import os
import pty
import select
import shlex
import signal
import struct
import subprocess
import termios
import threading

from .. import containers, service

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def read_frame(rfile) -> tuple[int, bytes] | None:
    head = rfile.read(2)
    if len(head) < 2:
        return None
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", rfile.read(8))[0]
    if length > 1 << 20:
        return None  # a terminal never needs a megabyte in one frame
    mask = rfile.read(4) if masked else b""
    data = bytearray(rfile.read(length))
    if masked:
        for i in range(len(data)):
            data[i] ^= mask[i % 4]
    return opcode, bytes(data)


def frame(payload: bytes, opcode: int = 2) -> bytes:
    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return header + payload


def shell_command(name: str, app: dict) -> tuple[list[str], dict | None, str | None]:
    """What to run for this app, and where."""
    shell = "if command -v bash >/dev/null; then exec bash; else exec sh; fi"
    if app.get("kind") == "db":
        return ([*shlex.split(containers.docker()), "exec", "-it", containers.container_name(name),
                 "sh", "-c", shell], None, None)
    if app.get("kind") == "compose":
        base = [*shlex.split(containers.docker()), "compose", "-p", containers.compose_project(name)]
        services = subprocess.run([*base, "ps", "--services"], capture_output=True, text=True,
                                  cwd=app["workdir"]).stdout.split()
        target = next((s for s in ("web", "app", "api", "server") if s in services),
                      services[0] if services else "app")
        return [*base, "exec", target, "sh", "-c", shell], None, app["workdir"]
    env = dict(os.environ, PORT=str(app["port"]), HOIST_APP=name, TERM="xterm-256color",
               **(app.get("env") or {}))
    return ["/bin/bash", "-l"] if os.path.exists("/bin/bash") else ["/bin/sh", "-l"], env, app["workdir"]


def bridge(rfile, wfile, argv: list[str], env: dict | None, cwd: str | None) -> None:
    """Run `argv` in a PTY and shuttle bytes until either side goes away."""
    master, slave = pty.openpty()
    env = dict(env or os.environ, TERM="xterm-256color")
    proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                            start_new_session=True, close_fds=True)
    os.close(slave)
    lock = threading.Lock()
    alive = threading.Event()
    alive.set()

    def send(payload: bytes, opcode: int = 2) -> None:
        with lock:
            wfile.write(frame(payload, opcode))
            wfile.flush()

    def pump_out() -> None:
        try:
            while alive.is_set():
                ready, _, _ = select.select([master], [], [], 0.5)
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                send(data)
        except OSError:
            pass
        finally:
            alive.clear()
            try:
                send(b"\r\n[session ended]\r\n")
                send(struct.pack("!H", 1000), opcode=8)
            except OSError:
                pass

    threading.Thread(target=pump_out, daemon=True).start()
    try:
        while alive.is_set():
            got = read_frame(rfile)
            if got is None:
                break
            opcode, data = got
            if opcode == 8:
                break
            if opcode == 9:
                send(data, opcode=10)
                continue
            if opcode not in (1, 2) or not data:
                continue
            kind, body = data[:1], data[1:]
            if kind == b"d":
                os.write(master, body)
            elif kind == b"r":
                try:
                    cols, rows = (int(x) for x in body.decode().split(","))
                    fcntl.ioctl(master, termios.TIOCSWINSZ,
                                struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0))
                    os.killpg(proc.pid, signal.SIGWINCH)
                except (ValueError, OSError):
                    pass
    except OSError:
        pass
    finally:
        alive.clear()
        try:
            os.killpg(proc.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        os.close(master)


__all__ = ["accept_key", "bridge", "frame", "read_frame", "service", "shell_command"]
