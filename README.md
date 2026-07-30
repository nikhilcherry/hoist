# hoist

**Put a local app on a public HTTPS URL, with one command.**

```
$ hoist up ./my-demo
· my-demo · package.json (npm start) · port 43581
  → $ npm start
✓ listening on 127.0.0.1:43581
✓ ingress rule added /etc/cloudflared/config.yml
✓ DNS → my-demo.example.com
✓ cloudflared reloaded

  https://my-demo.example.com
  hoist logs my-demo   ·   hoist down my-demo

  ▄▄▄▄▄▄▄  ▄ ▄▄  ▄▄▄▄▄▄▄
  █ ▄▄▄ █ ▀█▄▀▄█ █ ▄▄▄ █
  █ ███ █ █▄ ▄▀█ █ ███ █
  █▄▄▄▄▄█ █ ▀ ▀ █ █▄▄▄▄▄█
   ... (scannable, in your terminal)
```

That's it. Your app is running as a managed service, it survives crashes and
reboots, it has a real certificate, and the QR code on screen puts it on a
judge's phone in about two seconds.

## Why

Getting a local project onto a shareable URL is five fiddly steps that are
easy to get wrong under time pressure:

1. pick a port nothing else is using
2. write a systemd unit so it keeps running
3. add an ingress rule to your Cloudflare Tunnel config
4. create the DNS record
5. restart the tunnel, then find the URL again to share it

`hoist up` is those five steps. `hoist down` is their exact inverse.

## Install

```bash
pipx install hoist-cli        # or: pip install --user hoist-cli
```

Or straight from source, no packaging step:

```bash
git clone https://github.com/nikhilcherry/hoist
cd hoist && pip install -e .
```

**Zero runtime dependencies** — standard library only, including the QR
encoder. It works on a conference laptop with no internet.

Check your setup any time:

```bash
hoist doctor
```

## Requirements

- Linux with `systemd --user`
- [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
  with a tunnel already created, and a domain on Cloudflare
- Python 3.9+

Only want a LAN URL? Skip cloudflared entirely and use `--local`.

## Commands

| Command | What it does |
| --- | --- |
| `hoist up [dir]` | Run a project and publish it. Detects the start command. |
| `hoist share [dir]` | Serve a folder of files publicly. No project needed. |
| `hoist ls` | Every hoisted app, its port, health and URL. |
| `hoist down <name>` | Stop it and remove its ingress rule. |
| `hoist logs <name> [-f]` | Tail the app's journald logs. |
| `hoist restart <name>` | Restart the service. |
| `hoist url <name>` | Print the URL (pipe it into `xdg-open`, Slack, etc). |
| `hoist qr <name>` | Reprint the QR code. |
| `hoist adopt <name> --port N` | Publish something already running (Docker, `npm run dev`). |
| `hoist doctor` | Check systemd, cloudflared, tunnel, domain, permissions. |

### Useful flags

```bash
hoist up ./api --name api --port 8000        # pin the name and port
hoist up ./api --cmd 'uvicorn main:app --port $PORT'
hoist up ./api --env DATABASE_URL=postgres://localhost/dev
hoist up ./site --local                      # LAN URL + QR, no tunnel, no internet
hoist up ./site --domain example.com         # remembered for next time
```

## What it detects

| Found in the directory | Start command |
| --- | --- |
| `Procfile` with a `web:` line | that line |
| `package.json` | `npm start`, else `npm run dev` / `serve` / `preview` |
| `manage.py` | `python3 manage.py runserver 127.0.0.1:$PORT` |
| `app.py` / `main.py` / `server.py` / `wsgi.py` | `python3 <file>` |
| `Cargo.toml` | `cargo run --release` |
| `go.mod` | `go run .` |
| `index.html` or any `*.html` | `python3 -m http.server $PORT` |

Anything else: pass `--cmd`. Your app should bind the port in `$PORT`, which
hoist sets in the environment and substitutes into the command.

## Safety around your tunnel config

The cloudflared config is usually a live file with hand-written rules in it,
so hoist is deliberately careful:

- Every rule it adds is tagged with a `# hoist:<name>` marker, and it will
  only ever remove rules carrying its own marker.
- It **refuses** to touch a hostname you configured by hand.
- The config is edited as text, so your comments, ordering and indentation
  survive. `hoist down` restores the file byte-for-byte.
- Every write makes a timestamped backup first.
- After writing it runs `cloudflared tunnel ingress validate`, and rolls back
  to the backup if the config is rejected.
- The catch-all rule is always kept last, where cloudflared requires it.

`hoist down` intentionally leaves the DNS record in place: deleting DNS is
slow to undo, and reusing the same name later just works.

## Hackathon notes

- **Offline?** `--local` gives you a LAN URL and a QR code with no internet
  and no cloudflared. Everyone on the venue wifi can reach your demo.
- **Demo day.** `hoist qr <name>` fills the terminal with a code judges can
  scan while you talk. No "let me just find the link".
- **Webhooks.** Stripe, Twilio, GitHub and friends need a real HTTPS URL. A
  hoisted app gives you a stable one that survives laptop sleep.
- **Your laptop lid.** Services are `Restart=on-failure` and enabled, so they
  come back after a reboot. Run `sudo loginctl enable-linger $USER` once so
  they also survive logout — `hoist doctor` reminds you.

## How it works

```
hoist up ./app
   │
   ├─ detect.py   pick a free port, work out the start command
   ├─ service.py  write ~/.config/systemd/user/hoist-<name>.service, start it
   ├─ tunnel.py   insert an ingress rule, validate, route DNS, reload
   └─ qr.py       render the URL as a scannable code
```

State lives in `~/.config/hoist/apps.json`. Nothing is hidden anywhere else:
hoist only ever touches that file, its own systemd units, and the ingress
rules it marked.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

The QR encoder is written from scratch against the spec so the tool has no
dependencies. It is tested two ways: a golden matrix in the unit tests, and
`scripts/qr_conformance.py`, which renders codes to PNG and decodes them with
`zbarimg` in CI.

## License

MIT
