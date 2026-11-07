# hoist vs Dokploy: a measured comparison

Everything here was measured on one machine, in one sitting, by running both
systems against the same app. The raw logs and the scripts that produced them
are in this repository, so you can check or repeat it.

**The machine:** Intel Core i7-13620H (16 threads), 23 GB RAM, Pop!_OS, Linux 7.0,
Docker 29.7.2. **Dokploy:** v0.31.0, installed with its official script on
2026-11-07. **hoist:** this repository at the commit that added `scripts/bench/`.
Both ran on the same Docker daemon, one after the other (never at the same time).

## The test

One tiny app, a Python file server in a `Dockerfile`, in one git repository.
Both systems clone that repository and build it with `docker build`. The same
five-step script drives each:

1. Push a commit that changes the page to `VERSION: <tag>`.
2. Fire a push webhook at the system under test (a signed GitHub-style `curl`
   for hoist, Dokploy's per-app webhook URL for Dokploy). The time of that
   request is **T0**.
3. A separate process requests the app's URL every 50 ms and logs what comes
   back. **Trigger→live** is the first response carrying the new tag, minus T0.
   **Gap** is the longest run of failed requests around the deploy.
4. Repeat for five deploys.
5. Push a build that fails (`RUN exit 1`) and check the old version keeps
   serving; then push a fix.

The poller, the runners and the analyser are `scripts/bench/`. Raw poll logs
(every 50 ms request) and trigger timestamps are in `docs/bench-results/`.

## Results

### Deploying

| | hoist | Dokploy |
| --- | --: | --: |
| Trigger→live, 5 deploys, median | **1.88 s** (1.12–1.95) | 2.53 s (2.48–2.63) |
| Gap while the new version replaces the old | **0.65–1.45 s** | **0.10 s** (see note) |
| Broken build: old version keeps serving | yes, no gap | yes, no gap |
| Fix after the broken build | 1.91 s | 2.66 s |

**Dokploy's rollover is much smoother.** The page never went away for more than
the poller's resolution (0.10 s is two 50 ms polls, an upper bound); I did not
investigate the mechanism. hoist stops the old container and starts the new
one, so a Dockerfile or Compose app has a gap of roughly a second per deploy. hoist is faster to *finish* because it does less
work around the build, but it is not zero-downtime for container apps. (Plain
process apps restart in well under a second.)

### Footprint and responsiveness

| | hoist | Dokploy |
| --- | --: | --: |
| Install | **6.7 s** (`pip install` in a fresh venv) | 236 s (pulls three images) |
| On disk | **0.9 MB** | **5.4 GB** (dokploy 4.51, postgres 0.64, traefik 0.25) |
| Needs | Python, systemd; Docker only for container apps | root, Docker Swarm, ports 80, 443 and 3000 |
| Idle memory | **29.6 MiB** | 638 MiB (dokploy 500, postgres 115, traefik 23) |
| Idle CPU, nothing watching | 0.30 % (0.70 % before the fix below) | **≈ 0.1 %** (0.07 + 0.00 + 0.03) |
| Dashboard first load | **283 ms, 18 requests, 460 KB** | 958 ms, 118 requests, 7.2 MB |
| API call (list of apps), median | **3.5 ms** (p95 5.3) | 24.1 ms (p95 36.8) |

**hoist idles at more CPU than Dokploy.** The figure first published here
(0.33 %) was wrong: it counted only hoist's own process and missed the
`systemctl` and `docker` child processes its monitor spawns on every sample.
Counted properly it was **0.70 %** of a core. The monitor now samples every 15 s
when nobody has the dashboard open (3 s while someone does), which brought it to
**0.30 %**. What remains is mostly the uptime check of each public URL every
30 s, a feature Dokploy does not have. Dokploy's figure is `docker stats` over
its three containers, so it includes all their processes.

## What this found about hoist

Measuring was worth doing: it turned up two real defects that the tests did not.

- **Every container redeploy waited 10 s for nothing.** The first hoist run
  took about 11 s per deploy. A plain `python` or `node` server as a
  container's PID 1 ignores SIGTERM, so Docker waited out its 10 s grace period
  (`docker stop`: 10.14 s without an init process, 0.11 s with one). The
  generated Compose files now set `init: true`, which is where the 1.1–1.9 s
  above comes from. The first deploy after the fix still showed 11.1 s in the
  logs because it replaced a container created before the fix.
- **Every dashboard API call took ~45 ms.** In the first browser measurement
  hoist's API answered in 44.6 ms against Dokploy's 24.1 ms, with a tight
  spread: the signature of Nagle's algorithm waiting on a delayed ACK, because
  the server sent headers and body as two small writes. `curl` had hidden it
  (it measured 1 ms). Buffering each response into one write and disabling
  Nagle brought it to 3.5 ms, and the dashboard's load time from 654 ms to
  283 ms. A regression test now fails without the fix.

## How far to trust this

- One laptop, one app, five deploys per system. The ranges are small, but this
  is not a statistical study and a different machine will give different
  numbers.
- The app is tiny, so builds are dominated by fixed costs. A real build would
  take longer on both and shrink the relative difference.
- The Docker layer cache was warm for both, the same way.
- hoist fetched the repository with `file://` and Dokploy over local HTTP (it
  clones with `--depth 1`, so it needed a smart-HTTP git server, which I ran on
  Docker's private bridge address). That slightly favours hoist.
- Dokploy was used as installed, with no tuning. Its deployment settings (for
  example update order or replicas) were left at their defaults.
- I wrote the hoist side and the harness. The harness treats both systems
  identically and the logs are included, but it is not an independent
  third-party benchmark.
- Only deploying, footprint and dashboard speed were measured. Features are
  compared separately in [vs-dokploy.md](vs-dokploy.md).

## Repeat it

```bash
# a bare repo with the app, a poller, then each runner (see the scripts' headers)
scripts/bench/run_hoist.sh   WORKDIR http://127.0.0.1:<hoist app port>/
scripts/bench/run_dokploy.sh WORKDIR <dokploy app webhook token>
python3 scripts/bench/analyze.py WORKDIR/hoist.poll WORKDIR/hoist.triggers
```
