#!/usr/bin/env bash
# Start Vite dev server for the React frontend.
# Vite proxy forwards /api/* to http://localhost:8000 (see web/vite.config.ts).
# Usage: ./scripts/dev/start-frontend.sh

set -euo pipefail

cd "$(dirname "$0")/../../web"

echo "[start-frontend] vite http://localhost:5173 (proxy /api -> :8000)"

# --host exposes on 0.0.0.0 in case the laptop talks to this host directly
# (otherwise vite binds to 127.0.0.1 only). For SSH port-forwarding the
# default localhost binding is enough — drop --host if you prefer that.
exec npm run dev -- --host
