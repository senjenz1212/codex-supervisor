# Good Implementation Plan Fixture

## Files / Modules To Touch

- `supervisor/planning_validator.py`
- `supervisor/dual_agent_runner.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `tests/test_planning_validator.py`

## Risks

- Heuristics can be too weak and allow shallow documents.
- Heuristics can be too strict and block legitimate concise plans.
- Receipt events can be hidden if transcript readers do not include the new kind.

## Traceability

- P1 -> test_run_dual_agent_gate_blocks_stub_prd_before_claude_invocation
- P2 -> test_planning_validator_fixture_matrix_accepts_good_and_rejects_stub_or_sneaky
- P3 -> test_read_gate_transcript_includes_planning_validation_receipts

## Steps

1. Add fixtures and RED tests.
2. Implement the pure validator.
3. Wire the runner and transcript receipt.
