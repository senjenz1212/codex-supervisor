# CS23 PRD Grill Findings — Telegram Mode Toggle Approval

## Finding 1 — Slash commands are still mutation surfaces

`/autosteer on` is more explicit than free text, but it still changes live
supervisor behavior. It must not bypass the same approval philosophy as other
mutations.

Resolution: CS23 requires every mode toggle command to create a `mode_change`
action and nonce-protected Telegram ask before config is changed.

## Finding 2 — Only two mode keys are in scope

Generic mode editing from Telegram would expose hook blocking, recovery, drift,
and other safety dials too early.

Resolution: CS23 allowlists only `steering_injection` and `telegram_fyis`.

## Finding 3 — Restart failure must stay visible

A config edit without a daemon restart would leave the operator thinking the
mode changed when the live process is still using the old config.

Resolution: approval applies the config update and invokes the restart runner;
errors mark the action `failed` instead of `applied`.
