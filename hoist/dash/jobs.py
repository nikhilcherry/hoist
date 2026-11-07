"""Long actions (deploy, redeploy, roll back, take down) run as hoist itself.

The dashboard never re-implements a command: it runs `python -m hoist ...`
in a subprocess and streams that output to the browser. What a person sees
in the dashboard is exactly what the terminal would have printed.
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

KEEP_JOBS = 40
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PKG_ROOT = Path(__file__).resolve().parent.parent.parent


class Job:
    _ids = itertools.count(1)

    def __init__(self, title: str, args: list[str], app: str | None) -> None:
        self.id = f"j{next(self._ids)}"
        self.title = title
        self.args = args
        self.app = app
        self.lines: list[str] = []
        self.status = "running"
        self.code: int | None = None
        self.started = time.time()
        self.ended: float | None = None
        self.changed = threading.Condition()

    def as_dict(self, with_lines: bool = False) -> dict:
        data = {
            "id": self.id, "title": self.title, "app": self.app,
            "command": "hoist " + " ".join(self.args),
            "status": self.status, "code": self.code,
            "started": self.started, "ended": self.ended,
        }
        if with_lines:
            data["lines"] = list(self.lines)
        return data


class Jobs:
    def __init__(self, on_finish=None) -> None:
        self.jobs: "OrderedDict[str, Job]" = OrderedDict()
        self.lock = threading.Lock()
        self.on_finish = on_finish

    def busy(self, app: str | None) -> Job | None:
        with self.lock:
            for job in self.jobs.values():
                if job.app == app and app is not None and job.status == "running":
                    return job
        return None

    def start(self, title: str, args: list[str], app: str | None = None) -> Job:
        job = Job(title, args, app)
        with self.lock:
            self.jobs[job.id] = job
            while len(self.jobs) > KEEP_JOBS:
                self.jobs.popitem(last=False)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def recent(self) -> list[dict]:
        with self.lock:
            return [job.as_dict() for job in reversed(self.jobs.values())]

    def _emit(self, job: Job, line: str) -> None:
        with job.changed:
            job.lines.append(_ANSI.sub("", line.rstrip("\n")))
            job.changed.notify_all()

    def _run(self, job: Job) -> None:
        env = dict(os.environ, NO_COLOR="1", PYTHONUNBUFFERED="1",
                   PYTHONPATH=str(_PKG_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "hoist", *job.args],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, errors="replace", env=env, cwd=str(Path.home()),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self._emit(job, line)
            code = proc.wait()
        except OSError as exc:
            self._emit(job, f"could not start hoist: {exc}")
            code = 1
        with job.changed:
            job.code = code
            job.status = "ok" if code == 0 else "failed"
            job.ended = time.time()
            job.changed.notify_all()
        if self.on_finish:
            self.on_finish(job)
