# hoist vs Dokploy

Dokploy is a good self-hosted PaaS for a server. hoist is built for a different
machine: the laptop on the hackathon table, behind college Wi-Fi, with a demo
in two hours. This page compares them feature by feature. Dokploy's column is
taken from its own documentation (links at the bottom); hoist's column is what
this repository does and its tests check. Where Dokploy is ahead, it says so.

## Measured on the same laptop

Both were run on one Pop!_OS laptop (16 threads, 23 GB RAM) against the same app;
the method, raw logs and caveats are in [benchmark.md](benchmark.md).

| | hoist | Dokploy v0.31.0 |
| --- | --: | --: |
| Install | 6.7 s | 236 s |
| On disk | 0.9 MB | 5.4 GB |
| Idle memory | 29.6 MiB | 638 MiB |
| Dashboard first load | 283 ms, 460 KB | 958 ms, 7.2 MB |
| Dashboard API call (median) | 3.5 ms | 24.1 ms |
| Push→live, Dockerfile app (median) | 1.54 s | 2.53 s |
| Downtime while a container app is replaced | 0.00 s (Dockerfile/buildpack apps) | ≈ 0.1 s |
| Idle CPU (nothing watching) | 0.30 % + 0.02 % per container app | ≈ 0.1 % |

Dokploy is ahead on idle CPU. hoist is ahead on the rest, including rollover for Dockerfile and buildpack apps (Compose stacks and templates still restart, on purpose).

## Features

✓ yes · ◐ partly · ✗ no · — not covered by the Dokploy docs checked

| | hoist | Dokploy | Notes |
| --- | :-: | :-: | --- |
| Deploy on `git push` | ✓ | ✓ | both accept a push webhook; hoist also polls every 20 s as a fallback that needs no inbound access. Timings in [benchmark.md](benchmark.md) |
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
| Team accounts and roles | ✓ | ✓ | hoist: owner, member, viewer and **custom** roles built from eight permissions (create, deploy, operate, config, backups, delete, terminal, secrets), limited to chosen apps and/or **environments** (production, staging, development, preview), each person with their own link. Dokploy's free tier has fixed Owner/Admin/Member roles with per-project, per-service and per-environment access; its custom roles are an Enterprise feature |
| Audit log | ✓ | ✓ | hoist: who deployed, rolled back, changed env (values never logged) or ran a command, free. In Dokploy audit logs are listed as an Enterprise feature |
| QR code for every app | ✓ | — | for putting the demo on a judge's phone |
| Several servers | ◐ | ✓ | hoist: link other machines' dashboards and manage them all from one (apps, deploys, logs, databases, backups), tokens kept server-side. Dokploy also scales one app across a Swarm cluster; hoist does not |
| Command palette, light and dark | ✓ | — | |

## What Dokploy still does that hoist does not

- Scale one app across a Docker Swarm cluster (hoist links separate machines; it does not schedule one app over them).
- Ship Nixpacks and Heroku/Paketo buildpacks built in. hoist uses them only if you installed them.
- A larger built-in template catalogue (hoist: 46, plus any Compose file you add).
- An Admin role, SSO (an Enterprise feature in Dokploy), and permission categories beyond hoist's eight (for example SSH-key and API/CLI access, which hoist does not have as separate concepts). hoist has environments as a label on an app (four fixed values), not Dokploy's separate environment objects per project.
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
