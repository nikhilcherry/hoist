#!/usr/bin/env bash
# Time hoist deploying a Dockerfile app pushed to a git repo (webhook trigger).
#   run_hoist.sh WORKDIR APP_URL          WORKDIR holds repo.git and work/ (see README)
# Writes WORKDIR/hoist.poll (what the app served, every 50 ms) and WORKDIR/hoist.triggers.
set -euo pipefail
W=$1; URL=$2; DASH=${DASH:-http://127.0.0.1:7457}
now() { python3 -c "import time;print(f'{time.time():.3f}')"; }
: > "$W/hoist.poll"; : > "$W/hoist.triggers"
python3 "$(dirname "$0")/poller.py" "$URL" "$W/hoist.poll" & POLLER=$!
trap 'kill $POLLER 2>/dev/null || true' EXIT
secret=$(hoist webhook bench | awk '/Secret/{print $NF}')
cd "$W/work"

push() {  # push VERSION [broken]
  echo "<h1>VERSION: $1</h1>" > site/index.html
  if [ "${2:-}" = broken ]; then
    printf 'FROM python:3.12-slim\nCOPY site /site\nRUN exit 1\nEXPOSE 8000\nCMD ["python","-m","http.server","8000","--directory","/site"]\n' > Dockerfile
  else
    printf 'FROM python:3.12-slim\nCOPY site /site\nEXPOSE 8000\nCMD ["python", "-u", "-m", "http.server", "8000", "--directory", "/site"]\n' > Dockerfile
  fi
  git add -A && git -c user.name=b -c user.email=b@b commit -qm "$1" && git push -q origin main
  git -C "$W/repo.git" update-server-info
}
trigger() {  # trigger VERSION [fail]  -> POST a signed GitHub-style webhook, log the time
  body='{"ref":"refs/heads/main"}'
  sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$secret" | awk '{print $NF}')
  t0=$(now)
  curl -s -o /dev/null -X POST "$DASH/hooks/bench" -H "X-Hub-Signature-256: sha256=$sig" -H "X-GitHub-Event: push" -d "$body"
  echo "$t0 $1 ${2:-}" >> "$W/hoist.triggers"
}
wait_live() { for _ in $(seq 1 1200); do grep -q " 200 $1 " "$W/hoist.poll" && return 0; sleep 0.1; done; return 1; }

sleep 2
for i in 1 2 3 4 5; do push "h$i"; trigger "h$i"; wait_live "h$i" || echo "h$i never went live"; sleep 4; done
push hbroken broken; trigger hbroken fail; sleep 75        # a build that fails
push hfix; trigger hfix; wait_live hfix || echo "hfix never went live"; sleep 4
echo done
