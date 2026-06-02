# TDD Plan: Reviewer Panel Adjudication

## test_run_dual_agent_workflow_split_panel_triggers_adjudication

Maps to: P1, P3, P4, Slice 1

RED: A split panel blocks through the conservative evaluator but does not record
a structured adjudication packet over the strongest objection.

GREEN: Add adjudication detection and packet assembly. The workflow still
blocks, and `independent_reviewer_panel_decision.adjudication` records the
strongest reviewer, objection, severity, hashes, refs, and evidence check
status.

## test_run_dual_agent_workflow_accept_with_strong_objection_escalates

Maps to: P2, P4, Slice 1

RED: Two accepting reviewers advance even when one critical review carries an
important strongest objection.

GREEN: Treat important/critical strongest objections on accept results as
strong-minority adjudication triggers. The adjudicated result escalates, Codex
records a non-accept reviewer decision, and the gate does not auto-advance.

## test_real_reviewer_revise_still_hard_blocks_with_adjudication

Maps to: P3, Slice 1

RED: Adding adjudication risks changing the existing hard-block result for real
important revise/deny verdicts.

GREEN: Keep the existing conservative `revise` decision and reason while
adding adjudication metadata. The workflow status and cursor decision remain
blocked/non-accept.

## test_independent_reviewer_adjudication_event_and_transcript_export

Maps to: P1, P4, Slice 1

RED: Replay artifacts and `read_gate_transcript` only expose reviewer results
and panel decision, not the adjudication packet.

GREEN: Write an `independent_reviewer_adjudication` event, include it in
`read_gate_transcript`, and render adjudication metadata in exported
interactions/transcript markdown.

## test_reviewer_panel_adjudication_checks_bounded_refs

Maps to: P4, Slice 1

RED: The adjudication packet can cite refs and hashes but has no bounded
evidence inspection result.

GREEN: Inspect only local workspace-relative refs up to a small cap, report
`verified`, `hash_mismatch`, `missing`, `skipped_external`, or
`skipped_unbounded`, and never read outside cwd.

## test_panel_decision_is_exported_on_new_and_legacy_reviewer_events

Maps to: P3, P4, Slice 4

Regression: preserve the legacy and new reviewer event export path while
adjudication metadata is added.

## test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept

Maps to: P3, P4, Slice 4

Regression: preserve the rule that missing reviewer verdicts never count as
accept.

## test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection

Maps to: P3, Slice 4

Regression: preserve byte-stable legacy Cursor rejection behavior after
adjudication helper fixtures were extended.

## test_second_reviewer_outage_proceeds_only_degraded

Maps to: P3, P4, Slice 4

Regression: preserve the reviewer-unavailable degraded recovery path so
adjudication cannot override a classified infrastructure outage.

## Regression Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_split_panel_triggers_adjudication tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_accept_with_strong_objection_escalates tests/test_dual_agent_workflow_driver.py::test_real_reviewer_revise_still_hard_blocks_with_adjudication tests/test_dual_agent_workflow_driver.py::test_independent_reviewer_adjudication_event_and_transcript_export tests/test_dual_agent_workflow_driver.py::test_reviewer_panel_adjudication_checks_bounded_refs -q`
- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_reviewer_panel_adjudication_checks_bounded_refs tests/test_dual_agent_artifacts.py -q`
- `uv run --extra dev pytest -q`
