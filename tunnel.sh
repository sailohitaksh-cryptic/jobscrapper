#!/usr/bin/env bash
# Run the dashboard locally and expose it on a public https link via a
# Cloudflare quick tunnel (no account needed). Press Ctrl-C to stop both.
set -euo pipefail

# run from the repo dir no matter where this is called from
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# the tunnel needs cloudflared
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install it, then re-run:"
  echo "  brew install cloudflared        # macOS"
  echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  exit 1
fi

# start the dashboard in the background
python -m uvicorn app:app --host 127.0.0.1 --port "$PORT" --log-level warning &
SERVER_PID=$!

# always stop the server when this script exits
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "dashboard running at http://127.0.0.1:$PORT"
echo "opening public tunnel — grab the https://<...>.trycloudflare.com link below:"
echo

# foreground: cloudflared prints the public URL; Ctrl-C stops everything
cloudflared tunnel --url "http://127.0.0.1:$PORT"
