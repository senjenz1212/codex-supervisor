# Issue 02: Resolve Named Codex Desktop Sessions

## What to build

Resolve human-facing session names such as "Vela chat bot" to run id, session
id, rollout path, and status. Use explicit aliases first, then run metadata,
stored event content, and bounded rollout content.

## PRD Promise

**Promise IDs:** CS2

**Public boundary:** `named_session_resolver`

**Allowed outcomes:** exact alias resolves; content match resolves; ambiguous
matches return candidates; no match returns helpful recent candidates.

**Forbidden outcomes:** treats "Vela chat bot" as Slack by default; guesses
between ambiguous sessions; prefers stale completed sessions over current active
matches.

## TDD Plan

RED: Resolve "Vela chat bot" from a fake Codex rollout and assert the result is
a Codex run, not a Slack source.

GREEN: Add `supervisor/named_session.py`.

## Status

Implemented. Covered by `tests/test_named_session_resolver.py`.
