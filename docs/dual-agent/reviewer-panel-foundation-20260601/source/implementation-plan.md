# Implementation Plan: Reviewer Panel Foundation

## Scope

Implement the reviewer panel foundation as a schema/API compatibility slice.
Keep single-reviewer decision semantics unchanged while adding panel-shaped
results, a small registry boundary, a new event kind, export/read support, tests,
and ADR documentation.

## Steps

1. Add a reviewer panel/result module or small helpers that map the existing
   reviewer invocation result into `independent_reviewer_results[]`.
2. Add a thin reviewer-registry interface with mock reviewer support and a real
   structured Gemini/LiteLLM adapter that reuses existing typed outcome
   validation.
3. Wire workflow execution to attach panel results while retaining
   `cursor_review` and `independent_reviewer` compatibility aliases.
4. Emit `independent_reviewer_review` events alongside
   `tri_agent_cursor_review`.
5. Extend read-side allowlists, transcript projection, failure taxonomy, and
   artifact rendering so both new and legacy event shapes are preserved.
6. Add ADR documentation for the boundary rename and dual-write migration.
7. Add public-boundary tests for workflow result shape, event emission,
   transcript/export rendering, registry mocks, real structured reviewer adapter,
   legacy fixture compatibility, and single-reviewer decision equivalence.

## Files / Modules To Touch

- `supervisor/reviewer_registry.py`
- `supervisor/cursor_agent.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `supervisor/state.py`
- `supervisor/dual_agent_artifacts.py`
- `supervisor/failure_taxonomy.py`
- `supervisor/agent_mailbox.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_dual_agent_artifacts.py`
- `tests/test_cursor_agent.py`
- `docs/adr/20260601-independent-reviewer-panel-boundary.md`

## Risks

- The new event could accidentally replace the legacy event and break replay
  consumers that only know `tri_agent_cursor_review`.
- The panel list could accidentally become aggregation logic and change gate
  acceptance before the dedicated aggregation slice.
- Transcript or output hashes could be computed over unstable structures unless
  the implementation chooses deterministic JSON/text serialization.
- Mock reviewer support could grow into a plugin framework unless the registry
  remains small and explicitly scoped to configured reviewer specs.

## Traceability

- P1 maps to
  `test_workflow_exposes_independent_reviewer_results_for_single_reviewer` and
  `test_artifact_export_renders_panel_reviewer_results`.
- P2 maps to
  `test_reviewer_registry_supports_mock_panel_and_real_structured_reviewer`.
- P3 maps to `test_workflow_emits_independent_reviewer_review_and_legacy_event`,
  `test_read_gate_transcript_reads_new_and_legacy_reviewer_events`, and
  `test_artifact_export_renders_panel_reviewer_results`.
- P4 maps to `test_single_reviewer_decision_regression_is_equivalent`.
- P5 maps to `test_artifact_export_renders_panel_reviewer_results` and the
  outcome-review documentation checks.

## Guardrails

- Do not change aggregation or gate decision semantics.
- Do not remove legacy event kinds or payload fields.
- Do not require a second vendor reviewer.
- Do not weaken typed reviewer outcome validation or reviewer-unavailable
  recovery rules.
- Do not log secrets.

## Validation

- Focused tests:
  `uv run --extra dev pytest tests/test_dual_agent_workflow_driver.py tests/test_dual_agent_artifacts.py -q`
- Full suite:
  `uv run --extra dev pytest -q`
