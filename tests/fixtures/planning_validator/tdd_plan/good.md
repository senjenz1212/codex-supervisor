# Good TDD Fixture

## Public Boundary

Use `dual_agent_planning_validator` and `dual_agent_runner`.

## Test Cases

### test_planning_validator_fixture_matrix_accepts_good_and_rejects_stub_or_sneaky

Maps to: ISS-1, P2
RED: Load every fixture kind and assert only `good.md` passes.
GREEN: Implement deterministic section, count, phrase, and status checks.

### test_run_dual_agent_gate_blocks_stub_prd_before_claude_invocation

Maps to: ISS-2, P1
RED: Call the runner with a stub PRD and assert the fake runner has zero calls.
GREEN: Run the validator before writing the handoff packet.

### test_read_gate_transcript_includes_planning_validation_receipts

Maps to: ISS-2, P3
RED: Run a blocked planning gate and assert the transcript exposes check IDs.
GREEN: Persist planning validation receipts and include them in transcript reads.

## RED/GREEN Plan

RED: Add one public-boundary failing test for the fixture matrix.
GREEN: Add the smallest validator checks that make good pass and stubs fail.

RED: Add one public-boundary failing test for runner pre-invocation blocking.
GREEN: Wire the validator into `run_dual_agent_gate`.
