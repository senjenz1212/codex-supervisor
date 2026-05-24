# Planning Artifact Validator Implementation Plan

## Files / Modules To Touch

- `supervisor/planning_validator.py`: new deterministic validator module with
  typed artifact and aggregate results.
- `supervisor/dual_agent_runner.py`: call the validator before handoff packet
  creation and worker invocation; include a planning probe in gate results.
- `supervisor/state.py`: include planning-validation receipt events in
  dual-agent event reads.
- `mcp_tools/codex_supervisor_stdio.py`: pass state into runner calls and
  expose planning validation receipts in `read_gate_transcript`.
- `tests/test_planning_validator.py`: fixture matrix and traceability tests.
- `tests/test_dual_agent_runner.py`: runner-level block and receipt tests.
- `tests/test_codex_supervisor_mcp_stdio.py`: relaxed-policy and transcript
  tests.
- `tests/test_dual_agent_workflow_driver.py`: accepted workflow docs helper and
  auto-seeded stub regression.
- `docs/testing/public-boundaries.md`: add the
  `dual_agent_planning_validator` boundary.
- `docs/testing/dual-agent-slice0-coverage-index.md`: name the new evidence.

## Risks

- False positives can make honest iteration too heavy. Mitigation: checks are
  deterministic, fixture-driven, and scoped to lifecycle-critical artifacts.
- Heuristics can become shallow theater. Mitigation: every kind has a sneaky
  fixture that passes heading detection but fails substance.
- Runner-level validation may break existing tests that used tiny fake docs.
  Mitigation: update accepted-path tests to create substantive fixtures and add
  a regression proving auto-seeded docs block.
- Receipt events can be written twice if validation is split between MCP and
  runner. Mitigation: the runner owns validation and writes the receipt only
  when a state handle is supplied.

## Traceability

- P1 -> test_run_dual_agent_gate_blocks_stub_prd_before_claude_invocation
- P1 -> test_start_dual_agent_gate_relaxed_artifact_policy_still_blocks_stub_planning
- P1 -> test_run_dual_agent_workflow_blocks_auto_seeded_planning_stubs
- P2 -> test_planning_validator_fixture_matrix_accepts_good_and_rejects_stub_or_sneaky
- P3 -> test_planning_validator_blocks_unresolved_plan_traceability
- P4 -> test_read_gate_transcript_includes_planning_validation_receipts

## Execution Steps

1. Add the spec doc and fixtures so the intended checks are reviewable before
   implementation.
2. Implement the pure validator and make fixture tests pass.
3. Wire the validator into `run_dual_agent_gate` and add runner tests that prove
   Claude is not invoked on failed planning validation.
4. Pass the state handle from MCP runner calls and expose receipt events in the
   transcript tool.
5. Update workflow tests to pre-create substantive docs for accepted paths and
   assert auto-seeded stubs block.
6. Run focused tests, the full pytest suite, compileall, and diff checks.

## Non-Bypass Rules

- No validation check may call an LLM, live Claude, live Cursor, live Telegram,
  Browser, or Computer Use.
- `artifact_policy="relaxed"` cannot skip planning substance.
- Operator-level planning waivers are not implemented in this slice.
- A grill finding with `status: open` blocks; a waived grill finding requires a
  non-empty reason in the artifact itself.
