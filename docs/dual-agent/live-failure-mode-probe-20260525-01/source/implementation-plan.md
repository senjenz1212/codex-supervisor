# Live Failure Mode Probe Implementation Plan

## Files / Modules To Touch

- `scripts/probe_live_failure_mode.py`
- `supervisor/redaction.py`
- `tests/test_dual_agent_live_lead_fixture.py`
- `tests/test_redaction_pipeline.py`
- `docs/testing/dual-agent-slice0-live-evidence.md`

## Risks

- A live model may return a different but valid schema, so the replay fixture
  should preserve the exact stdout for future parser regression tests.
- Cursor SDK errors may include sensitive material, so redaction must cover the
  standalone Cursor key format before writing new artifacts.
- A blocked probe could be misread as an execution failure unless the summary
  clearly distinguishes accepted Claude output from rejected supervisor claims.

## Traceability

- P1 -> test_live_failure_mode_probe_replays_claude_fixture_and_preserves_p11_block
- P2 -> test_live_failure_mode_probe_cursor_fixture_is_redacted_and_parseable
- P3 -> test_live_failure_mode_probe_replays_claude_fixture_and_preserves_p11_block

## Steps

1. Add Cursor-key redaction coverage.
2. Add a live failure-mode probe script.
3. Capture live Claude and Cursor outputs in sanitized fixtures.
4. Add replay tests for the captured evidence.
5. Update the live evidence docs with the final blocked status.
