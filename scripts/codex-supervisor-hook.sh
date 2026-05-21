#!/usr/bin/env bash
set -euo pipefail

target="${1:-codex}"
url="${CODEX_SUPERVISOR_HOOK_URL:-http://127.0.0.1:19001/hook/${target}}"

curl -fsS \
  -X POST "${url}" \
  -H "Content-Type: application/json" \
  --data-binary @-
