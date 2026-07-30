#!/usr/bin/env bash
# Regenerate the screenshots in docs/ from real hoist runs.
#
#   scripts/make_docs_images.sh            # --local screenshots only
#   scripts/make_docs_images.sh --public NAME
#
# The --public form runs a real publish against your tunnel, which rewrites the
# cloudflared config and restarts the tunnel. Pass a hostname you already use
# for testing so it does not create a new DNS record.
#
# Captures go through `script` so hoist believes it is on a terminal and emits
# colour; scripts/ansi_to_svg.py turns that into an SVG.

set -euo pipefail

cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

public_name=""
if [[ "${1:-}" == "--public" ]]; then
    public_name="${2:?--public needs an app name}"
fi

# A predictable interpreter keeps the detected start command short in the
# screenshots; a conda or venv python bakes its whole path into the image.
export PATH=/usr/bin:/bin
hoist() { script -qec "python3 -m hoist $*" /dev/null; }

mkdir -p "$tmp/my-demo"
cat > "$tmp/my-demo/index.html" <<'HTML'
<!doctype html><meta charset=utf-8><title>my-demo</title>
<h1>my-demo is live</h1>
HTML

render() { python3 scripts/ansi_to_svg.py "$1" "$2" --title "$3"; }

# ── local: no tunnel, no network, nothing to clean up beyond the service ──
hoist down my-demo >/dev/null 2>&1 || true
hoist up "$tmp/my-demo" --name my-demo --local > "$tmp/local.ansi" 2>&1
render "$tmp/local.ansi" docs/demo-local.svg "hoist up ./my-demo --local"

# ── public: a real publish, so the ingress and verify lines are genuine ───
if [[ -n "$public_name" ]]; then
    hoist down "$public_name" >/dev/null 2>&1 || true
    hoist up "$tmp/my-demo" --name "$public_name" > "$tmp/public.ansi" 2>&1
    render "$tmp/public.ansi" docs/demo-up.svg "hoist up ./my-demo"
fi

hoist ls > "$tmp/ls.ansi" 2>&1
render "$tmp/ls.ansi" docs/demo-ls.svg "hoist ls"

# `doctor` prints the real tunnel id, which is infrastructure detail nobody
# needs in a README. Check the hostname list by eye before committing too.
hoist doctor 2>&1 \
    | sed -E 's/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}/8f14a2c1-4b7e-4c9a-9e13-6d2f0a5b7c31/' \
    > "$tmp/doctor.ansi"
render "$tmp/doctor.ansi" docs/demo-doctor.svg "hoist doctor"

hoist down my-demo >/dev/null 2>&1 || true

echo
echo "Check the QR codes still decode:"
for svg in docs/demo-up.svg docs/demo-local.svg; do
    [[ -f "$svg" ]] || continue
    png="$tmp/$(basename "$svg" .svg).png"
    if command -v rsvg-convert >/dev/null; then
        rsvg-convert "$svg" -o "$png"
    elif command -v google-chrome >/dev/null; then
        google-chrome --headless --disable-gpu --screenshot="$png" \
            --window-size=900,900 "file://$PWD/$svg" >/dev/null 2>&1
    else
        echo "  (no renderer; skipping $svg)" && continue
    fi
    command -v zbarimg >/dev/null && zbarimg --quiet "$png" || echo "  (zbarimg missing)"
done
