# Live Failure Mode Probe Issues

## Slice ISS-1: Live Claude Receipt Gap Fixture

Type: AFK
Priority: P0
Estimate: S
Scope: Run a live Claude Code `/lead` outcome-review gate that returns a typed
accepted outcome with implementation and test claims.
PRD promise: P1, P3
First public-boundary RED test: `test_live_failure_mode_probe_replays_claude_fixture_and_preserves_p11_block`

Acceptance Criteria:
- [ ] Live stdout fixture exists under `tests/fixtures/dual_agent/live_failure_mode_probe_20260525_01`.
- [ ] Replaying the fixture yields a valid typed outcome.
- [ ] Summary records P11 red with missing test and diff receipt failures.

## Slice ISS-2: Cursor Reviewer Trace

Type: AFK
Priority: P1
Estimate: S
Scope: Run Cursor as an optional read-only reviewer and preserve the typed
review transcript without exposing credentials.
PRD promise: P2, P3
First public-boundary RED test: `test_live_failure_mode_probe_cursor_fixture_is_redacted_and_parseable`

Acceptance Criteria:
- [ ] Cursor transcript is parseable when the live key is available.
- [ ] Cursor summary contains only boolean key presence and operational IDs.
- [ ] Secret scan finds no `crsr_` token in probe artifacts.
