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

## Finding 4 — Restart can fail without raising

The production restart runner returns a structured result with `ok: false` on
non-zero `launchctl`, so tests must not only cover thrown exceptions.

Resolution: `test_mode_toggle_restart_failure_is_not_reported_as_success`
drives the Telegram approve callback with a fake restart result of `ok: false`
and verifies the action is `failed`, the config file is rolled back, and the
callback reports failure instead of success.

## Finding 5 — The issue examples did not cover the full command matrix

The PRD promises four commands, but the initial TDD plan only covered
`/autosteer on` and `/quiet on`.

Resolution: `test_mode_toggle_off_commands_are_approval_gated` covers
`/autosteer off` and `/quiet off` through the same Telegram command/callback
boundary.

## Finding 6 — Defensive authority checks need boundary proof

CS23 relies on configured-chat authority, nonce expiry, and the two-key
allowlist. Helper-only tests would not prove those against Telegram callback
handling.

Resolution: `test_mode_toggle_command_from_wrong_chat_is_ignored`,
`test_mode_toggle_expired_callback_fails_closed`, and
`test_mode_toggle_non_allowlisted_key_fails_closed` cover those forbidden
outcomes at the poller boundary.
