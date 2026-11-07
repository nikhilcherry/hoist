#!/usr/bin/env bash
# Time Dokploy deploying the same Dockerfile app, triggered by its push webhook.
#   run_dokploy.sh WORKDIR REFRESH_TOKEN      Dokploy at localhost:3000, app routed as bench.localhost on :80
# Mirrors run_hoist.sh step for step: five deploys, a build that fails, a fix.
set -euo pipefail
W=$1; TOKEN=$2; DOKPLOY=${DOKPLOY:-http://localhost:3000}
now() { python3 -c "import time;print(f'{time.time():.3f}')"; }
: > "$W/dokploy.poll"; : > "$W/dokploy.triggers"
python3 "$(dirname "$0")/poller.py" http://127.0.0.1:80/ "$W/dokploy.poll" --host bench.localhost & POLLER=$!
trap 'kill $POLLER 2>/dev/null || true' EXIT
cd "$W/work"

push() {  # push VERSION [broken]
  echo "<h1>VERSION: $1</h1>" > site/index.html
  if [ "${2:-}" = broken ]; then
    printf 'FROM python:3.12-slim\nCOPY site /site\nRUN exit 1\nEXPOSE 8000\nCMD ["python","-m","http.server","8000","--directory","/site"]\n' > Dockerfile
  else
    printf 'FROM python:3.12-slim\nCOPY site /site\nEXPOSE 8000\nCMD ["python", "-u", "-m", "http.server", "8000", "--directory", "/site"]\n' > Dockerfile
  fi
  git add -A && git -c user.name=b -c user.email=b@b commit -qm "$1" && git push -q origin main
}
trigger() {  # trigger VERSION [fail]
  t0=$(now)
  curl -s -o /dev/null -m 20 -X POST "$DOKPLOY/api/deploy/$TOKEN" -H "Content-Type: application/json" \
    -H "X-GitHub-Event: push" -d '{"ref":"refs/heads/main","repository":{"full_name":"bench/bench"},"head_commit":{"message":"x"}}'
  echo "$t0 $1 ${2:-}" >> "$W/dokploy.triggers"
}
wait_live() { for _ in $(seq 1 1200); do grep -q " 200 $1 " "$W/dokploy.poll" && return 0; sleep 0.1; done; return 1; }

sleep 2
for i in 1 2 3 4 5; do push "d$i"; trigger "d$i"; wait_live "d$i" || echo "d$i never went live"; sleep 4; done
push dbroken broken; trigger dbroken fail; sleep 75
push dfix; trigger dfix; wait_live dfix || echo "dfix never went live"; sleep 4
echo done
