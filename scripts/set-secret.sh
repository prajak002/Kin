#!/usr/bin/env bash
# Save a secret into .env without it appearing on screen or in shell history.
#   scripts/set-secret.sh KIN_TELEGRAM_BOT_TOKEN
set -euo pipefail
cd "$(dirname "$0")/.."
name="${1:?usage: scripts/set-secret.sh NAME}"
read -rsp "$name: " value; echo
[ -n "$value" ] || { echo "empty, nothing saved"; exit 1; }
touch .env
grep -v "^$name=" .env > .env.tmp || true
printf '%s=%s\n' "$name" "$value" >> .env.tmp
mv .env.tmp .env
echo "saved $name to .env"
