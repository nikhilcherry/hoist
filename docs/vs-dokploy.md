# hoist vs Dokploy

Dokploy is a good self-hosted PaaS for a server. hoist is built for a different
machine: the laptop on the hackathon table, behind college Wi-Fi, with a demo
in two hours. This page compares them feature by feature. Dokploy's column is
taken from its own documentation (links at the bottom); hoist's column is what
this repository does and its tests check. Where Dokploy is ahead, it says so.

## Footprint, measured on the same laptop

Both were installed on one Pop!_OS laptop (16 cores, 23 GB RAM) in November 2026.

| | hoist | Dokploy |
| --- | --- | --- |
| Install | `pip install hoist-cli` (user, no root) | `curl … \| sudo sh`: root, Docker Swarm, ports 80, 443 and 3000 |
| On disk | **516 KB** (package, fonts included) | **5.4 GB** of images (dokploy 4.51 GB, postgres 642 MB, traefik 251 MB) |
| Dashboard memory | **28 MB** RSS | not measured here |
| Dependencies | Python standard library, systemd; Docker only for databases and stacks | Docker, Swarm, Traefik, Postgres |
| Dashboard response | `/api/state` in about **1 ms** | — |
| Public URL | Cloudflare Tunnel: no open ports, works behind NAT and campus Wi-Fi | Traefik on 80/443 with Let's Encrypt: needs a reachable server |
| Works with no internet | yes (`--local` gives a LAN URL and QR; fonts bundled) | — |

## Features

✓ yes · ◐ partly · ✗ no · — not covered by the Dokploy docs checked

| | hoist | Dokploy | Notes |
| --- | :-: | :-: | --- |
| Deploy on `git push` | ✓ | ✓ | hoist polls every 20 s (no inbound webhook needed); Dokploy uses webhooks, so it is faster to react |
| Builds | ◐ | ✓ | hoist: npm/pnpm/yarn, Python venv, Procfile, Dockerfile, Compose. Dokploy adds Nixpacks and Heroku/Paketo buildpacks |
| Docker Compose stacks | ✓ | ✓ | hoist builds a stack before it goes live, so a broken Dockerfile never replaces the running one |
| A failed build never takes the app down | ✓ | — | hoist builds each commit in its own release and swaps a symlink only on success; Dokploy documents automatic rollback on failed Swarm health checks |
| Rollbacks | ✓ | ✓ | hoist: one click to any commit, instant when the release is still on disk; Dokploy: Swarm or registry-based |
| Preview deployment per branch | ✓ | ✓ | hoist creates one per branch, removes it when the branch is deleted, caps at 5 |
| Databases | ✓ | ✓ | Both: PostgreSQL, MySQL, MariaDB, MongoDB, Redis; Dokploy also LibSQL (seen in its own database schema). hoist binds them to 127.0.0.1 with a generated password and a private volume |
| Link a database to an app | ✓ | — | `hoist link db app` / one button writes `DATABASE_URL` (or `REDIS_URL`…) and restarts the app |
| Database backups | ✓ | ✓ | hoist: daily, keep 10, restore, download, copy off-site to any rclone remote (S3, R2, B2, Drive) |
| Volume backups (non-database) | ✗ | ✓ | |
| One-click app templates | ◐ | ✓ | hoist ships 7 (Uptime Kuma, n8n, ntfy, Excalidraw, PocketBase, Metabase, Open WebUI); Dokploy's catalogue is far larger |
| Environment variables | ✓ | ✓ | hoist: editor, paste a `.env`, values hidden by default, unit files `0600` |
| Resource limits | ✓ | ✓ | hoist: memory and CPU; on containers they go to Docker, not to systemd |
| Live logs | ✓ | ✓ | hoist: app output and the deploy log, search, follow, download |
| Run a command in the app | ◐ | ✓ | hoist: one command at a time with streamed output; Dokploy: an interactive terminal |
| CPU, memory, disk, network | ✓ | ✓ | hoist measures containers from their cgroups, not the docker client |
| External uptime checks of each public URL | ✓ | — | every 30 s, with a one-hour strip and "why it is down" in plain words |
| Notifications | ✓ | ✓ | hoist: Discord, Slack, Telegram, ntfy, any JSON webhook; Dokploy also sends email |
| Team accounts | ◐ | ✓ | hoist: one owner, any number of members with their own links, an audit log; Dokploy has finer-grained permissions |
| Audit log | ✓ | — | who deployed, rolled back, changed env (values never logged), ran a command |
| QR code for every app | ✓ | — | for putting the demo on a judge's phone |
| Several servers / clustering | ✗ | ✓ | Dokploy can deploy to remote servers and scale across a Swarm. hoist runs one machine, by design |
| Command palette, light and dark | ✓ | — | |

## When to use which

- **A laptop at a hackathon, or a home box behind NAT:** hoist. No root install,
  no open ports, half a megabyte, and the demo survives a bad push.
- **A real server, several servers, or a big catalogue of apps:** Dokploy.

## Sources

- Dokploy features: <https://docs.dokploy.com/docs/core/features>
- Dokploy rollbacks: <https://docs.dokploy.com/docs/core/applications/rollbacks>
- Dokploy v0.24.0 (rollbacks, volume backups): <https://dokploy.com/blog/v0-24-0-rollbacks-docker-volume-backups-more>
- Dokploy vs CapRover (Dokploy's own comparison): <https://dokploy.com/dokploy-vs-caprover>
- Image sizes: `docker images` after installing Dokploy with its official script on the test laptop.
