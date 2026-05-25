# Live Failure Mode Probe TDD

## Public Boundary

Use `scripts/probe_live_failure_mode.py`, `invoke_claude_lead`, and
`verify_workflow_claims`.

## Test Cases

### test_live_failure_mode_probe_replays_claude_fixture_and_preserves_p11_block

Maps to: ISS-1, P1, P3
RED: Load the live stdout fixture and assert the summary contains a blocked P11
receipt failure.
GREEN: Write the probe script and captured fixture.

### test_live_failure_mode_probe_cursor_fixture_is_redacted_and_parseable

Maps to: ISS-2, P2, P3
RED: Load the Cursor transcript and assert it parses as a Cursor Reviewer
outcome with no raw Cursor key in fixture files.
GREEN: Run the live Cursor review and write redacted artifacts.

## RED/GREEN Plan

RED: Add replay tests that require live fixtures and receipt-failure fields.
GREEN: Run the live failure-mode probe and persist sanitized fixtures.

RED: Add secret-scan expectations for Cursor key shape.
GREEN: Extend redaction and rerun the Cursor-backed probe.
