# TDD Plan: Reviewer Panel Foundation

## test_workflow_exposes_independent_reviewer_results_for_single_reviewer

Maps to: P1, P2, P4

RED: A workflow with cursor review enabled exposes only `cursor_review` and
`independent_reviewer`; there is no `independent_reviewer_results[]`.

GREEN: The same workflow exposes `independent_reviewer_results[0]` with
reviewer id, runtime, model, provider lineage, tool access, assurance grade,
transcript refs, output/transcript hashes, decision, severity, and confidence.
The legacy `cursor_review` payload remains present and the gate decision is
unchanged.

## test_reviewer_registry_supports_mock_panel_and_real_structured_reviewer

Maps to: P2

RED: Reviewer invocation is hard-coded to one Cursor-named path and cannot be
exercised as a registry with multiple mock reviewers.

GREEN: A small registry returns mock reviewer specs/results for tests and a real
structured Gemini/LiteLLM reviewer adapter that reuses the existing typed
outcome path.

## test_workflow_emits_independent_reviewer_review_and_legacy_event

Maps to: P3, P4

RED: New runs write only `tri_agent_cursor_review`, so panel consumers have no
truthful event kind.

GREEN: New runs write `independent_reviewer_review` containing
`independent_reviewer_results[]` and still write `tri_agent_cursor_review` with
the legacy payload.

## test_read_gate_transcript_reads_new_and_legacy_reviewer_events

Maps to: P3

RED: `read_gate_transcript` allowlists only `tri_agent_cursor_review` and cannot
surface `independent_reviewer_review`.

GREEN: Transcript reads include new panel events and continue to include legacy
events. Old fixture replay remains readable.

## test_artifact_export_renders_panel_reviewer_results

Maps to: P1, P3, P5

RED: `interactions.md` and `transcript.md` render only Cursor-named reviewer
fields and omit panel metadata.

GREEN: Artifacts render the new panel event, per-reviewer metadata, transcript
refs, hashes, severity, confidence, and the ADR; legacy Cursor review rendering
continues to work.

## test_single_reviewer_decision_regression_is_equivalent

Maps to: P4

RED: Introducing a panel list could accidentally change accept/block behavior.

GREEN: Representative single-reviewer accept and revise/deny runs produce the
same `codex_decision` and final status as current main.

## Regression Commands

- `uv run --extra dev pytest tests/test_dual_agent_workflow_driver.py tests/test_dual_agent_artifacts.py -q`
- `uv run --extra dev pytest -q`
