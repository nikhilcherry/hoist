"""Tell the team when a deploy lands, a push breaks, or an app stops answering.

One webhook URL, set with `hoist notify <url>` or from the dashboard. The
payload shape is picked from the URL, so a Discord, Slack, ntfy or Telegram
link works as pasted; anything else gets a small JSON document.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

from . import config

TIMEOUT = 8.0


def kind(url: str) -> str:
    if url.startswith(("smtp://", "smtps://")):
        return "email"
    host = urllib.parse.urlsplit(url).netloc.lower()
    path = urllib.parse.urlsplit(url).path
    if host.endswith("discord.com") or host.endswith("discordapp.com"):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    if host == "api.telegram.org" and "/sendMessage" in path:
        return "telegram"
    if host.startswith("ntfy.") or host == "ntfy.sh":
        return "ntfy"
    return "json"


def _request(url: str, title: str, text: str, level: str, app: str | None) -> urllib.request.Request:
    style = kind(url)
    line = f"{title}\n{text}" if text else title
    headers = {"User-Agent": "hoist", "Content-Type": "application/json"}
    if style == "discord":
        body = {"content": line[:1900]}
    elif style == "slack":
        body = {"text": line}
    elif style == "telegram":
        body = {"text": line, "disable_web_page_preview": True}
    elif style == "ntfy":
        tags = {"ok": "white_check_mark", "bad": "rotating_light", "warn": "warning"}.get(level, "")
        return urllib.request.Request(url, data=text.encode() or title.encode(), method="POST", headers={
            "User-Agent": "hoist", "Title": title.encode("ascii", "replace").decode(),
            "Tags": tags, "Priority": "high" if level == "bad" else "default"})
    else:
        body = {"title": title, "text": text, "level": level, "app": app, "source": "hoist"}
    return urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)


def _email(url: str, title: str, text: str) -> tuple[bool, str]:
    """smtps://user:password@smtp.gmail.com:465/?to=team@example.com[,more]&from=…"""
    import smtplib
    import ssl
    from email.message import EmailMessage

    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    to = [a for v in query.get("to", []) for a in v.split(",") if a]
    if not to:
        return False, "the email URL needs ?to=address"
    user = urllib.parse.unquote(parts.username or "")
    password = urllib.parse.unquote(parts.password or "")
    sender = (query.get("from") or [user or "hoist@localhost"])[0]
    msg = EmailMessage()
    msg["Subject"] = f"[hoist] {title}"
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(f"{title}\n\n{text}\n\n— hoist")
    try:
        if parts.scheme == "smtps":
            server = smtplib.SMTP_SSL(parts.hostname, parts.port or 465, timeout=TIMEOUT,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(parts.hostname, parts.port or 587, timeout=TIMEOUT)
            if (parts.port or 587) != 25 and (query.get("starttls") or ["yes"])[0] != "no":
                server.starttls(context=ssl.create_default_context())
        with server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        return True, f"email to {len(to)} address(es)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def send(title: str, text: str = "", level: str = "info", app: str | None = None,
         url: str | None = None, wait: bool = True) -> tuple[bool, str]:
    """Post one notification. Never raises: a broken webhook must not break a deploy."""
    url = url or (config.load().get("notify") or {}).get("url")
    if not url:
        return False, "no webhook configured"

    def go() -> tuple[bool, str]:
        if kind(url) == "email":
            return _email(url, title, text)
        try:
            with urllib.request.urlopen(_request(url, title, text, level, app), timeout=TIMEOUT) as r:
                return True, f"HTTP {r.status}"
        except Exception as exc:  # noqa: BLE001 — report, never raise
            return False, str(getattr(exc, "reason", exc))[:200]

    if wait:
        return go()
    threading.Thread(target=go, daemon=True).start()
    return True, "queued"
