#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/sam.zhang/Documents/codex-supervisor"
SECRETS="$HOME/.codex-supervisor/secrets.env"

if [ -f "$SECRETS" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$SECRETS"
  set +a
fi

export CODEX_SUPERVISOR_CONFIG="${CODEX_SUPERVISOR_CONFIG:-$HOME/.codex-supervisor/config.yaml}"
"$ROOT/.venv/bin/codex-supervisor-workflow-dispatcher" \
  --config "$CODEX_SUPERVISOR_CONFIG" \
  >> /tmp/codex-supervisor-dispatcher.out \
  2>> /tmp/codex-supervisor-dispatcher.err &
DISPATCHER_PID="$!"

cleanup() {
  kill "$DISPATCHER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec "$ROOT/.venv/bin/python" "$ROOT/daemon.py"
