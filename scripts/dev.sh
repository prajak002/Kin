#!/usr/bin/env bash
# Run Kin locally: MCP server (:8000), agent (:8080) and the web app (:3000).
# The agent uses the running MCP server, so everything shares one store.
set -euo pipefail
cd "$(dirname "$0")/.."

export KIN_MCP_URL="${KIN_MCP_URL:-http://127.0.0.1:8000/mcp}"

trap 'kill 0' EXIT INT TERM
uv run kin-mcp &
uv run python -m kin.agent &
(cd web && pnpm dev) &
wait
