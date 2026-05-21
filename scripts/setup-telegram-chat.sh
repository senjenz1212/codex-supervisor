#!/usr/bin/env bash
set -euo pipefail

SECRETS="$HOME/.codex-supervisor/secrets.env"
if [ ! -f "$SECRETS" ]; then
  echo "Missing $SECRETS" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN is missing in $SECRETS" >&2
  exit 1
fi

chat_id="$(
  python3 - <<'PY'
import json, os, urllib.parse, urllib.request

token = os.environ["TELEGRAM_BOT_TOKEN"]
url = (
    f"https://api.telegram.org/bot{token}/getUpdates?"
    + urllib.parse.urlencode({
        "limit": 20,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    })
)
with urllib.request.urlopen(url, timeout=20) as r:
    data = json.loads(r.read().decode())

if not data.get("ok"):
    raise SystemExit(data.get("description") or "getUpdates failed")

for update in reversed(data.get("result", [])):
    msg = update.get("message") or {}
    cb = update.get("callback_query") or {}
    if cb and not msg:
        msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("id") is not None:
        print(chat["id"])
        raise SystemExit(0)

raise SystemExit("No chat id found. Send /start to the bot, then rerun this script.")
PY
)"

tmp="$SECRETS.tmp"
umask 077
{
  grep -v '^TELEGRAM_CHAT_ID=' "$SECRETS" || true
  printf 'TELEGRAM_CHAT_ID=%s\n' "$chat_id"
} > "$tmp"
mv "$tmp" "$SECRETS"
chmod 600 "$SECRETS"

echo "telegram_chat_id_saved"
