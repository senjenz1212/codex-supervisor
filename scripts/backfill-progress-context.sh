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
exec "$ROOT/.venv/bin/python" -m supervisor.progress_backfill --config "$CODEX_SUPERVISOR_CONFIG" "$@"
