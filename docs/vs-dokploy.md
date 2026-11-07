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
| Deploy on `git push` | ✓ | ✓ | hoist: a signed GitHub/GitLab webhook goes live in **0.27 s** (median of 5, below), with a 20 s poll as fallback that needs no inbound access |
| Builds | ◐ | ✓ | hoist: npm/pnpm/yarn, Python venv, Procfile, Dockerfile, Compose, and Nixpacks or `pack` **if you already have them installed** (hoist never installs build tools; exercised with a stand-in tool honouring the Nixpacks command line, not the real one). Dokploy bundles Nixpacks and Heroku/Paketo buildpacks |
| Docker Compose stacks | ✓ | ✓ | hoist builds a stack before it goes live, so a broken Dockerfile never replaces the running one |
| A failed build never takes the app down | ✓ | — | hoist builds each commit in its own release and swaps a symlink only on success; Dokploy documents automatic rollback on failed Swarm health checks |
| Rollbacks | ✓ | ✓ | hoist: one click to any commit, instant when the release is still on disk; Dokploy: Swarm or registry-based |
| Preview deployment per branch | ✓ | ✓ | hoist creates one per branch, removes it when the branch is deleted, caps at 5 |
| Databases | ✓ | ✓ | Both: PostgreSQL, MySQL, MariaDB, MongoDB, Redis; Dokploy also LibSQL (seen in its own database schema). hoist binds them to 127.0.0.1 with a generated password and a private volume |
| Link a database to an app | ✓ | — | `hoist link db app` / one button writes `DATABASE_URL` (or `REDIS_URL`…) and restarts the app |
| Database backups | ✓ | ✓ | hoist: daily, keep 10, restore, download, copy off-site to any rclone remote (S3, R2, B2, Drive) |
| Volume backups (non-database) | ✓ | ✓ | hoist archives every volume of a Docker stack in one file, with its containers paused for consistency; restore and download |
| One-click app templates | ◐ | ✓ | hoist ships 46 with generated passwords, and `hoist template add <Compose file or URL>` turns any Compose file into your own template, so the catalogue is open-ended. Dokploy's built-in catalogue is larger |
| Environment variables | ✓ | ✓ | hoist: editor, paste a `.env`, values hidden by default, unit files `0600` |
| Resource limits | ✓ | ✓ | hoist: memory and CPU; on containers they go to Docker, not to systemd |
| Live logs | ✓ | ✓ | hoist: app output and the deploy log, search, follow, download |
| Terminal in the app | ✓ | ✓ | hoist: an interactive terminal (PTY over WebSocket, xterm.js bundled) in the app's folder, database container or stack; plus one-shot commands |
| CPU, memory, disk, network | ✓ | ✓ | hoist measures containers from their cgroups, not the docker client |
| External uptime checks of each public URL | ✓ | — | every 30 s, with a one-hour strip and "why it is down" in plain words |
| Notifications | ✓ | ✓ | hoist: Discord, Slack, Telegram, ntfy, email (SMTP) and any JSON webhook |
| Team accounts and roles | ✓ | ✓ | hoist: owner, member, viewer and **custom** roles built from six permissions (deploy, operate, config, backups, delete, secrets), limited to chosen apps, each person with their own link. Dokploy's free tier has fixed Owner/Admin/Member roles with per-project, per-service and per-environment access; its custom roles are an Enterprise feature |
| Audit log | ✓ | ✓ | hoist: who deployed, rolled back, changed env (values never logged) or ran a command, free. In Dokploy audit logs are listed as an Enterprise feature |
| QR code for every app | ✓ | — | for putting the demo on a judge's phone |
| Several servers | ◐ | ✓ | hoist: link other machines' dashboards and manage them all from one (apps, deploys, logs, databases, backups), tokens kept server-side. Dokploy also scales one app across a Swarm cluster; hoist does not |
| Command palette, light and dark | ✓ | — | |

## Measured: push to live

`scripts/bench_deploy.sh` makes a throwaway repo, deploys it with hoist, pushes
a change and times how long until the new page is served. On the test laptop:

| Trigger | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 |
| --- | --: | --: | --: | --: | --: |
| GitHub-style webhook | 273 ms | 266 ms | 267 ms | 284 ms | 296 ms |
| 20 s poll (no webhook) | 4.8 s | 21.0 s | 20.6 s | 21.5 s | 21.0 s |

A failed build is measured by the test suite instead of a timer: the live
release's files are checked untouched after a build that deletes them and
exits non-zero (`test_a_broken_build_never_touches_the_files_being_served`).

## What Dokploy still does that hoist does not

- Scale one app across a Docker Swarm cluster (hoist links separate machines; it does not schedule one app over them).
- Ship Nixpacks and Heroku/Paketo buildpacks built in. hoist uses them only if you installed them.
- A larger built-in template catalogue (hoist: 46, plus any Compose file you add).
- Per-environment access (production vs staging), an Admin role, SSO (an Enterprise feature in Dokploy), and finer permission categories such as API/CLI and SSH-key access. hoist's permissions are the six listed above.
- Interactive-terminal parity on remote machines: hoist opens the terminal on that machine's own dashboard.

## When to use which

- **A laptop at a hackathon, or a home box behind NAT:** hoist. No root install,
  no open ports, half a megabyte, and the demo survives a bad push.
- **A real server, several servers, or a big catalogue of apps:** Dokploy.

## Sources

- Dokploy features: <https://docs.dokploy.com/docs/core/features>
- Dokploy permissions and roles: <https://docs.dokploy.com/docs/core/permissions>
- Dokploy custom roles (Enterprise): <https://docs.dokploy.com/docs/core/enterprise/custom-roles>
- Dokploy role-based access control: <https://dokploy.com/features/role-based-access-control>
- Dokploy rollbacks: <https://docs.dokploy.com/docs/core/applications/rollbacks>
- Dokploy v0.24.0 (rollbacks, volume backups): <https://dokploy.com/blog/v0-24-0-rollbacks-docker-volume-backups-more>
- Dokploy vs CapRover (Dokploy's own comparison): <https://dokploy.com/dokploy-vs-caprover>
- Image sizes: `docker images` after installing Dokploy with its official script on the test laptop.
