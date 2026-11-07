#!/usr/bin/env python3
"""Turn a poller log plus a list of triggers into deploy-time and downtime numbers.

    analyze.py POLLER_LOG TRIGGERS_FILE

TRIGGERS_FILE has one `epoch_seconds version [expect]` per line: the moment a
deploy was triggered and the version it should bring up. `expect` is `fail`
for a deliberately broken build, where the right outcome is the OLD version
still serving and no gap. Prints, per trigger: seconds until the new version
was first seen, and the longest stretch of failed requests around it.
"""

from __future__ import annotations

import statistics
import sys


def load(path: str):
    rows = []
    for line in open(path):
        parts = line.split()
        if len(parts) == 3:
            rows.append((float(parts[0]), int(parts[1]), parts[2]))
    return rows


def main() -> int:
    rows = load(sys.argv[1])
    triggers = [line.split() for line in open(sys.argv[2]) if line.strip()]
    live, gaps = [], []
    print(f"{'version':<12}{'outcome':<26}{'trigger→live':>14}{'longest gap':>14}")
    for i, trig in enumerate(triggers):
        t0, version = float(trig[0]), trig[1]
        expect_fail = len(trig) > 2 and trig[2] == "fail"
        end = float(triggers[i + 1][0]) if i + 1 < len(triggers) else rows[-1][0]
        window = [r for r in rows if t0 <= r[0] <= end]
        seen = next((r[0] for r in window if r[2] == version and r[1] == 200), None)
        # longest run of non-200 responses in the window, from request timestamps
        gap, run_start, prev = 0.0, None, None
        for t, status, _ in window:
            if status != 200:
                run_start = run_start if run_start is not None else (prev if prev is not None else t)
            elif run_start is not None:
                gap = max(gap, t - run_start)
                run_start = None
            prev = t
        if run_start is not None:
            gap = max(gap, window[-1][0] - run_start)
        if expect_fail:
            outcome = "old version kept serving" if seen is None and gap < 0.5 else "BROKE the live app"
            print(f"{version:<12}{outcome:<26}{'—':>14}{gap:>12.2f} s")
        elif seen is None:
            print(f"{version:<12}{'never went live':<26}{'—':>14}{gap:>12.2f} s")
        else:
            live.append(seen - t0)
            gaps.append(gap)
            print(f"{version:<12}{'live':<26}{seen - t0:>12.2f} s{gap:>12.2f} s")
    if live:
        print(f"\nsuccessful deploys: {len(live)}   median {statistics.median(live):.2f} s   "
              f"min {min(live):.2f} s   max {max(live):.2f} s")
        print(f"downtime during deploy (longest gap): median {statistics.median(gaps):.2f} s   max {max(gaps):.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
