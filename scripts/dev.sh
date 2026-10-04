#!/usr/bin/env bash
# Run Kin locally the way Vercel does: one Python backend (MCP, agent, webhooks)
# on :8000 and the web app on :3000.
set -euo pipefail
cd "$(dirname "$0")/.."

export KIN_BACKEND_URL="${KIN_BACKEND_URL:-http://127.0.0.1:8000}"

trap 'kill 0' EXIT INT TERM
uv run uvicorn kin.server:app --port 8000 &
(cd web && pnpm dev) &
wait
