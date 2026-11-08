# Security

## Reporting a vulnerability

Please report it privately through GitHub's **Report a vulnerability** button
(Security tab of this repository) instead of opening a public issue. Expect a
first reply within a few days.

## What to know before exposing hoist

- The dashboard has a terminal and a command console, so a sign-in link is as
  powerful as a shell on the machine. Treat links like passwords.
- `hoist dash` binds to `127.0.0.1` by default. `--public` publishes it behind
  the same token; prefer Cloudflare Access in front of it for anything beyond a
  demo.
- Every write must carry a header token as well as the cookie, so a page on
  another site cannot drive your dashboard.
- hoist edits your cloudflared config only inside rules it tagged
  `# hoist:<name>`, backs the file up first, and rolls back if
  `cloudflared tunnel ingress validate` rejects it.
- Secrets in app environments are kept out of generated Compose files.
- If a link leaks, run `hoist dash --reset-token` (signs everyone out) or
  `hoist dash --remove-user NAME`.
