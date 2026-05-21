# Grounded Review Suggestions PRD Grill Findings

## Status

All findings resolved in the CS9 promise and issue slice before implementation.

## Findings

### GR1. "Ground Truth" Must Mean Bounded Workspace Evidence

Finding: The user intent says Claude should have "access to the code base or
folders", but unconstrained filesystem access would violate the supervisor's
redaction and target-adapter boundaries.

Resolution: CS9 defines ground truth as a bounded, read-only workspace snapshot:
resolved root, git status, git diff, changed files, and explicit safe file
reads. Reads must stay under the resolved workspace root and be redacted.

### GR2. Keep the Language Target-Agnostic

Finding: The current live source is Codex rollout events, but ADR-0001 says
supervisor core should stay target-agnostic where possible.

Resolution: The product term is "target workspace"; the v1 implementation
derives it from Codex rollout metadata and command workdirs, but the tool API
returns target-neutral workspace data.

### GR3. Suggestions Must Not Become Hidden Actions

Finding: "Provide suggestions and steer direction" can blur into automatic
target mutation.

Resolution: CS9 is notification-only. Reviews may send Telegram advice and
write verdicts, but must not call target adapters, inject steering, kill,
restart, or modify workspace files.

### GR4. Avoid Backfill and Noise Review Storms

Finding: The rollout watcher can ingest historical or low-signal events. If
every event triggers a model review, Telegram becomes noisy and expensive.

Resolution: Automatic review is triggered only after a watched run receives a
high-signal `task_complete` progress notification. Token-count, reasoning,
tool-noise, and unnotified backfill events do not enqueue reviews.
