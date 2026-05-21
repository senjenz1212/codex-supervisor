# CS23 TDD Grill Findings — Telegram Mode Toggle Approval

## Finding 1 — First RED must hit Telegram command handling

A helper that edits YAML would not prove the user-visible Telegram path is
safe.

Resolution: the first RED drives `TelegramPoller._handle_command` with
`/autosteer on` and verifies config stays unchanged until callback approval.

## Finding 2 — Rejection needs its own regression

Approval paths often pass while reject/cancel still mutates by accident.

Resolution: `test_quiet_command_reject_does_not_change_config_or_restart`
drives `/quiet on`, taps Reject, and verifies config plus restart state remain
unchanged.

## Finding 3 — Restart must be injectable

Tests should not actually restart launchd.

Resolution: `TelegramPoller` accepts `restart_runner`; production defaults to
`launchctl kickstart`, tests inject a fake runner below the public boundary.
