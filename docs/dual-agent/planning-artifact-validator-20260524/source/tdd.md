# Planning Artifact Validator TDD Plan

## Public Boundary

The first RED tests use approved public boundaries from
`docs/testing/public-boundaries.md`:

- `dual_agent_runner`
- `codex_supervisor_mcp`
- `dual_agent_planning_validator` (added by this slice to describe the pure
  deterministic validator boundary)

## Test Cases

### test_planning_validator_fixture_matrix_accepts_good_and_rejects_stub_or_sneaky

Maps to: ISS-1, P2
Boundary: `dual_agent_planning_validator`
RED: Load each fixture kind from `tests/fixtures/planning_validator` and assert
only `good.md` passes.
GREEN: Implement deterministic per-kind checks and aggregate verdicts.

### test_planning_validator_blocks_unresolved_plan_traceability

Maps to: ISS-1, P3
Boundary: `dual_agent_planning_validator`
RED: Validate a good implementation plan except for a nonexistent PRD promise
or TDD test reference and assert `PLAN-004` fails.
GREEN: Parse PRD promise IDs and TDD test names, then resolve traceability
references.

### test_run_dual_agent_gate_blocks_stub_prd_before_claude_invocation

Maps to: ISS-2, P1
Boundary: `dual_agent_runner`
RED: Call `run_dual_agent_gate(gate="prd_review")` with a stub PRD and a fake
runner that counts calls; assert `status="blocked"`, `attempts=0`, and call
count stays zero.
GREEN: Run planning validation before handoff writing and worker invocation.

### test_start_dual_agent_gate_relaxed_artifact_policy_still_blocks_stub_planning

Maps to: ISS-2, P1
Boundary: `codex_supervisor_mcp`
RED: Call `start_dual_agent_gate` with `artifact_policy="relaxed"` and a sneaky
PRD; assert it blocks with planning validation rather than invoking the fake
runner.
GREEN: Place the check below MCP artifact preflight in the runner.

### test_read_gate_transcript_includes_planning_validation_receipts

Maps to: ISS-3, P4
Boundary: `codex_supervisor_mcp`
RED: Run a blocked planning gate, then call `read_gate_transcript`; assert the
planning validation receipt includes failed check IDs and artifact hashes.
GREEN: Persist `dual_agent_planning_validation` events and include them in the
transcript response.

### test_run_dual_agent_workflow_blocks_auto_seeded_planning_stubs

Maps to: ISS-4, P1
Boundary: `codex_supervisor_mcp`
RED: Run `run_dual_agent_workflow` on a fresh task without operator-authored
source docs; assert the workflow blocks at `prd_review` and the fake worker is
not invoked.
GREEN: Let `ensure_workflow_source_artifacts` seed docs, but require those docs
to pass planning validation before a gate can execute.

## RED/GREEN Sequence

1. RED: fixture matrix for the pure validator.
   GREEN: implement section parsing, blocked phrase detection, count checks,
   and grill status checks.
2. RED: unresolved traceability case.
   GREEN: implement PRD/TDD reference extraction and PLAN-004 resolution.
3. RED: runner blocks stub PRD before invocation.
   GREEN: wire validator into `run_dual_agent_gate` and return a planning probe.
4. RED: relaxed policy cannot bypass.
   GREEN: keep validation inside the runner, not the MCP preflight.
5. RED: receipt appears in transcript.
   GREEN: write and read `dual_agent_planning_validation` events.
6. RED: workflow auto-seeded stubs block.
   GREEN: update workflow tests and require real source docs for accepted paths.

## Forbidden Outcomes Represented

- Dual-agent gates choose progress based on agent prose instead of verified
  artifacts.
- Dual-agent outcome review treats claims as verified action state without
  matching evidence.
- Replay calls live target agents, Telegram, or model APIs by default.
- Relaxed artifact policy bypasses a hard lifecycle gate.
