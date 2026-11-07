#!/usr/bin/env python3
"""Watch a URL every 50 ms and log what it serves, so a deploy can be timed from outside.

    poller.py URL OUTFILE [--host HOSTHEADER]

Each line is `epoch_seconds status version latency_ms`, where version is the text
after `VERSION:` in the response body ("-" when there is none, status 0 on a
connection error or timeout). The log is the ground truth for two numbers:
how long a push took to appear, and whether the old version ever stopped
answering while the new one came up.
"""

from __future__ import annotations

import http.client
import re
import sys
import time
import urllib.parse

PAT = re.compile(rb"VERSION:\s*([A-Za-z0-9._-]+)")


def main() -> int:
    url = urllib.parse.urlsplit(sys.argv[1])
    out = open(sys.argv[2], "a", buffering=1)
    host = sys.argv[sys.argv.index("--host") + 1] if "--host" in sys.argv else None
    while True:
        started = time.time()
        status, version = 0, "-"
        try:
            conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=1.0)
            conn.request("GET", url.path or "/", headers={"Host": host} if host else {})
            resp = conn.getresponse()
            body = resp.read(4096)
            status = resp.status
            match = PAT.search(body)
            version = match.group(1).decode() if match else "-"
            conn.close()
        except (OSError, http.client.HTTPException):
            pass
        out.write(f"{started:.3f} {status} {version} {(time.time() - started) * 1000:.0f}\n")
        time.sleep(max(0.0, 0.05 - (time.time() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
