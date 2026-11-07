"""Notifications: payload shapes per service, and real SMTP delivery."""

from __future__ import annotations

import json
import socketserver
import threading

import pytest

from hoist import notify


@pytest.mark.parametrize("url,kind", [
    ("https://discord.com/api/webhooks/1/x", "discord"),
    ("https://hooks.slack.com/services/a/b/c", "slack"),
    ("https://api.telegram.org/bot1:x/sendMessage?chat_id=5", "telegram"),
    ("https://ntfy.sh/my-team", "ntfy"),
    ("smtps://u:p@smtp.example.com:465/?to=a@b.c", "email"),
    ("https://example.com/hook", "json"),
])
def test_kind_is_read_from_the_url(url, kind):
    assert notify.kind(url) == kind


def test_discord_gets_content_and_json_gets_fields():
    req = notify._request("https://discord.com/api/webhooks/1/x", "t", "body", "ok", "web")
    assert json.loads(req.data) == {"content": "t\nbody"}
    req = notify._request("https://example.com/h", "t", "body", "bad", "web")
    assert json.loads(req.data)["level"] == "bad"


class _SMTP(socketserver.StreamRequestHandler):
    received: list[str] = []

    def handle(self):
        self.wfile.write(b"220 test\r\n")
        data_mode, lines = False, []
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if data_mode:
                if line == ".":
                    _SMTP.received.append("\n".join(lines))
                    data_mode = False
                    self.wfile.write(b"250 ok\r\n")
                else:
                    lines.append(line)
                continue
            verb = line.split(" ")[0].upper()
            if verb == "EHLO":
                self.wfile.write(b"250-test\r\n250 AUTH PLAIN\r\n")
            elif verb == "AUTH":
                self.wfile.write(b"235 ok\r\n")
            elif verb == "DATA":
                data_mode = True
                self.wfile.write(b"354 go\r\n")
            elif verb == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


def test_email_is_delivered_over_smtp():
    server = socketserver.TCPServer(("127.0.0.1", 0), _SMTP)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        ok, detail = notify.send("pulse stopped answering", "https://pulse.example answers 502",
                                 url=f"smtp://bot:pw@127.0.0.1:{port}/?to=a@team.dev,b@team.dev&starttls=no")
    finally:
        server.shutdown()
    assert ok, detail
    message = _SMTP.received[-1]
    assert "Subject: [hoist] pulse stopped answering" in message and "answers 502" in message


def test_a_broken_webhook_never_raises():
    ok, _ = notify.send("x", url="http://127.0.0.1:9/nothing-here")
    assert ok is False
