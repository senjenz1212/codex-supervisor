# TDD Plan: Reviewer Panel Conservative Aggregator

## test_run_dual_agent_workflow_panel_blocks_important_reviewer_revise

Maps to: P1, Slice 1, Slice 2

RED: A workflow with `independent_reviewer_results[]` still derives
`cursor_decision` directly from the legacy single result. The test should prove
that a serious reviewer revise or deny must create a panel decision with
blocking reviewer metadata and prevent advancement.

GREEN: Add the conservative panel evaluator and wire Codex decision composition
to use it. The workflow blocks, the panel decision is recorded, and legacy
single-reviewer block behavior remains equivalent.

## test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept

Maps to: P2, Slice 1, Slice 2

RED: A reviewer result with no typed outcome and no recoverable infrastructure
classification can be collapsed into a generic non-accepted single result
without explicit panel missing-verdict evidence.

GREEN: The panel decision records the missing reviewer verdict, returns a
non-accept decision, and Codex does not count it as accept.

## test_run_dual_agent_workflow_panel_high_confidence_accept_advances_by_default

Maps to: P3, Slice 2, Slice 3

RED: Routing through the panel evaluator could accidentally turn the normal
single Gemini/LiteLLM high-confidence accept path into escalation or block.

GREEN: With the default permissive threshold, a normal high-confidence accept
advances exactly as current main does and records an `accept` panel decision.

## test_run_dual_agent_workflow_panel_low_confidence_accept_escalates_when_threshold_configured

Maps to: P4, Slice 1, Slice 2, Slice 3

RED: Reviewer confidence is available on panel results but does not affect gate
decisions.

GREEN: With `reviewer_low_confidence_threshold` configured above the reviewer
confidence, the workflow records a panel escalation, Codex chooses a non-accept
decision, and the gate does not auto-advance.

## test_reviewer_unavailable_recovery_still_proceeds_degraded_with_panel_metadata

Maps to: P5, Slice 2, Slice 3

RED: Adding panel aggregation risks treating recoverable infrastructure failure
as a normal missing verdict and bypassing the existing recovery policy.

GREEN: The existing proceed-degraded reviewer-unavailable path still advances
only under its policy and available Claude/Codex accepts, records degraded
evidence, and includes panel metadata without counting the missing reviewer as
accept.

## test_panel_decision_is_exported_on_new_and_legacy_reviewer_events

Maps to: P3, P4, P5, Slice 2, Slice 3

RED: `independent_reviewer_review` and `tri_agent_cursor_review` events contain
panel results but no panel decision, so replay cannot explain the aggregate
choice.

GREEN: Both event payloads carry `independent_reviewer_panel_decision`, and
`read_gate_transcript` / exported artifacts retain the decision and per-reviewer
inputs.

## Regression Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_blocks_important_reviewer_revise tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_high_confidence_accept_advances_by_default tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_low_confidence_accept_escalates_when_threshold_configured tests/test_dual_agent_workflow_driver.py::test_reviewer_unavailable_recovery_still_proceeds_degraded_with_panel_metadata tests/test_dual_agent_workflow_driver.py::test_panel_decision_is_exported_on_new_and_legacy_reviewer_events -q`
- `uv run pytest tests/test_dual_agent_workflow_driver.py -q`
- `uv run --extra dev pytest -q`
