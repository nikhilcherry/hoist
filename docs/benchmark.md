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
| Trigger→live, 6 deploys, median | **1.54 s** (1.41–1.64) | 2.53 s (2.48–2.63) |
| Gap while the new version replaces the old | **0.00 s** (slowest request 10 ms) | ≈ 0.10 s (poller resolution) |
| Broken build: old version keeps serving | yes, no gap | yes, no gap |
| Fix after the broken build | 1.48 s | 2.66 s |

**hoist's first run lost on rollover; the second does not.** The first hoist run
(`bench-results/hoist-restart-based.*`) stopped the old container and started the
new one: 1.1–1.9 s to go live with a 0.65–1.45 s gap, against Dokploy's 0.10 s.
Docker apps that hoist builds from a Dockerfile or buildpack now start the new
container beside the old one behind a small supervisor (`hoist front`) that owns
the public port: it switches to the new container only once it answers HTTP,
drains the old one, and never refuses a connection. A new version that never
answers is discarded and the old one keeps serving. The figures above are that
version.

Seamless rollover covers **only** apps hoist builds itself from a Dockerfile or
buildpack. Compose stacks, ready-made templates and databases keep the simple
stop-and-start, on purpose: they may own data, and running two copies of a
database at once could corrupt it.

### Footprint and responsiveness

| | hoist | Dokploy |
| --- | --: | --: |
| Install | **6.7 s** (`pip install` in a fresh venv) | 236 s (pulls three images) |
| On disk | **0.9 MB** | **5.4 GB** (dokploy 4.51, postgres 0.64, traefik 0.25) |
| Needs | Python, systemd; Docker only for container apps | root, Docker Swarm, ports 80, 443 and 3000 |
| Idle memory | **29.6 MiB** | 638 MiB (dokploy 500, postgres 115, traefik 23) |
| Idle CPU, nothing watching | **0.07 %** (0.17 % with an alert webhook set; +0.02 % per container app) | ≈ 0.1 % (0.07 + 0.00 + 0.03) |
| Dashboard first load | **283 ms, 18 requests, 460 KB** | 958 ms, 118 requests, 7.2 MB |
| API call (list of apps), median | **3.5 ms** (p95 5.3) | 24.1 ms (p95 36.8) |

**Idle CPU took three steps to get here, and the first figure was wrong.** The
number first published here (0.33 %) counted only hoist's own process and missed the
`systemctl` and `docker` child processes its monitor spawns; counted properly it was
**0.70 %**. Sampling every 15 s when nobody is watching brought it to 0.30 %, and the
rest was probing every public URL every 30 s although nothing reads the results unless
someone is watching or an alert webhook is configured. Probes are now taken only then
(a viewer gets fresh ones immediately), giving **0.07 %**. With an alert webhook set the
probes must run, so it is **0.17 %**. Dokploy's figure is `docker stats` over its three
containers, so it includes all their processes. At 0.07 % the two are within noise of
each other; I would not call either one a win.

## What this found about hoist

Measuring was worth doing: it turned up defects that the tests did not, and it drove the rollover work above.

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

- **While building the rollover, benchmarking found three more.** Deploys six seconds
  apart (inside the old container's drain window) made the supervisor swap back to a
  stale container; a request arriving during container startup left it permanently
  without a target; and a first version polled Docker every 2 s by spawning the CLI,
  costing 3.3 % of a core per app (now 0.02 %, by asking the Docker Engine over its
  socket). Each has a regression test.

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
