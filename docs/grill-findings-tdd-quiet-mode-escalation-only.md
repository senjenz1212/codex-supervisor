# CS22 TDD Grill Findings — Quiet Mode Escalation Only

## Finding 1 — First proof must use watched-run progress

A config-loader test would only prove the YAML parses; it would not prove Sam
stops getting progress pings.

Resolution: the first RED targets `TelegramProgressStreamer.handle_event` with
an active watch and `telegram_fyi_mode="off"`, asserting no Telegram send,
quiet context persistence, watch advancement, and review enqueue.

## Finding 2 — Claude review messages need a separate boundary

`review_updates` suggestions are sent through the Telegram MCP tool, not the
progress streamer.

Resolution: `tests/test_telegram_quiet_mode.py` drives the MCP
`send_message` tool with fake SDK wrappers and proves `fyi` is suppressed while
`alert` still sends.

## Finding 3 — Suppressed must not look sent

If the model sees `{"sent": true}` for a suppressed message, it will tell Sam a
ping happened.

Resolution: quiet MCP suppression returns
`{"sent": false, "suppressed": true, "reason": "telegram_fyis_off"}`.

## Finding 4 — Quiet mode must not hide blocker progress

The live 18g launch produced a watched-run `HALTED before implementation`
message because the requested worktree was outside writable roots. Treating
that as a routine FYI made the supervisor record context but fail to ping Sam.

Resolution: `test_quiet_telegram_fyis_allows_halt_escalation_ping` drives
`TelegramProgressStreamer.handle_event` with the same HALT/sandbox-blocker text
shape and asserts quiet mode still sends Telegram, records the notification as
`[watched run alert]`, and marks urgency as `alert`.
