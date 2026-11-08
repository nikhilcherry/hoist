# Changelog

## 0.1.0

First public release.

- `hoist up` publishes a folder on a public HTTPS URL: free port, systemd user
  service, Cloudflare Tunnel ingress rule, DNS, and a QR code. `hoist down`
  is its exact inverse. `--local` gives a LAN URL with no tunnel.
- **Push to deploy**: `hoist up <git-url>` clones, builds and redeploys on every
  push (polling, or a webhook for ~0.3 s deploys). A failed build keeps the last
  good release serving; the last four releases are kept for instant rollback.
- **Docker apps**: Dockerfile, buildpack and Compose repos. Dockerfile and
  buildpack apps roll over with no downtime behind `hoist front`.
- **Databases** (PostgreSQL, MySQL, MariaDB, Redis, MongoDB), `hoist link`,
  scheduled backups, restore and off-site copy; 46 one-click templates.
- **Dashboard** (`hoist dash`): overview, route strip, deployments, rollback,
  live logs, environment editor, previews, terminal, per-person permissions
  and an audit log. Standard library only.
- Branch previews, resource limits, environments, alerts to Discord, Slack,
  ntfy, Telegram and email.
- Measured against Dokploy on one laptop: [docs/benchmark.md](docs/benchmark.md).
