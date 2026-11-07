#!/usr/bin/env bash
# Measure how long a git push takes to go live with hoist, by webhook and by
# the 20 s poll, on this machine. Creates a throwaway repo and app, then
# removes them. Needs a running `hoist dash` (for the webhook path).
set -euo pipefail
N=${N:-3}
DASH=${DASH:-http://127.0.0.1:7457}
WORK=$(mktemp -d)
trap 'hoist down bench-app >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
git init -q --bare -b main "$WORK/r.git"
git clone -q "$WORK/r.git" "$WORK/w" 2>/dev/null
cd "$WORK/w"
echo 'v0' > index.html
git add . && git -c user.name=b -c user.email=b@b commit -qm v0 && git push -q origin main
hoist up "file://$WORK/r.git" --name bench-app --local --no-qr >/dev/null
port=$(python3 -c "import json,os;p=os.environ.get('HOIST_HOME',os.path.expanduser('~/.config/hoist'));print(json.load(open(p+'/apps.json'))['apps']['bench-app']['port'])")
secret=$(hoist webhook bench-app | awk '/Secret/{print $NF}')

push() { echo "$1" > index.html; git -c user.name=b -c user.email=b@b commit -qam "$1"; git push -q origin main; }
wait_for() { until curl -s "localhost:$port/" | grep -q "$1"; do sleep 0.1; done; }
ms() { python3 -c "import time;print(int(time.time()*1000))"; }

echo "mode      run  push→live"
for i in $(seq 1 "$N"); do
  tag="hook$i-$RANDOM"; push "$tag"; t0=$(ms)
  body='{"ref":"refs/heads/main"}'
  sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$secret" | awk '{print $2}')
  curl -s -o /dev/null -X POST "$DASH/hooks/bench-app" -H "X-Hub-Signature-256: sha256=$sig" -H "X-GitHub-Event: push" -d "$body"
  wait_for "$tag"; echo "webhook   $i    $(( $(ms) - t0 )) ms"
done
for i in $(seq 1 "$N"); do
  tag="poll$i-$RANDOM"; push "$tag"; t0=$(ms)
  wait_for "$tag"; echo "poll      $i    $(( $(ms) - t0 )) ms"
done
