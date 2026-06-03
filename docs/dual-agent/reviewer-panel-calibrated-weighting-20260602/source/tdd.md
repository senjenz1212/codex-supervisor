# TDD Plan

Task id: `reviewer-panel-calibrated-weighting-20260602`

## test_reviewer_panel_eval_runner_emits_calibration_artifact_when_requested

Public boundary: `reviewer_panel_eval_runner`

Maps to: Slice 1, P1, P5

RED: calling the eval runner with calibration requested produces no
`reviewer-panel-calibration.json`, no active weight-change record, and no
source report hash.

GREEN: `emit_calibration=True` writes a calibration artifact with measured
pairwise dependency, reviewer weights, report hash, labeled-set hash, and
calibration digest.

## test_reviewer_panel_calibration_is_deterministic_and_data_derived

Public boundary: `build_reviewer_panel_calibration`

Maps to: Slice 1, P1, P5

RED: fixed eval input produces non-reproducible hashes, or two different eval
reports produce identical reviewer weights.

GREEN: fixed input produces identical `calibration_sha256`; a correlated eval
fixture produces different weights from an effectively independent fixture.

## test_run_dual_agent_workflow_calibrated_correlated_accept_escalates

Public boundary: `run_dual_agent_workflow`

Maps to: Slice 2, P2

RED: with a calibration artifact showing high failure overlap, two accepts
still advance as conservative all-accept.

GREEN: the panel decision uses `aggregation_mode=calibrated_weighted`, computes
weighted aggregate confidence below threshold, and escalates.

## test_run_dual_agent_workflow_calibrated_independent_accept_advances

Public boundary: `run_dual_agent_workflow`

Maps to: Slice 2, P2

RED: any active calibration blocks all unanimous accepts.

GREEN: effectively independent accepts produce aggregate confidence above the
threshold and advance.

## test_evaluate_reviewer_panel_calibrated_accept_applies_severity_multiplier

Public boundary: `evaluate_reviewer_panel`

Maps to: Slice 2, P2

RED: active eval-derived calibration ignores reviewer severity and treats an
important accept the same as a no-objection accept.

GREEN: calibrated accept payload records severity multipliers and important
accepts reduce aggregate confidence enough to escalate when they fall below the
calibration threshold.

## test_load_reviewer_panel_calibration_rejects_no_lineage_constants

Public boundary: `load_reviewer_panel_calibration`

Maps to: Slice 1, P1

RED: a hand-authored JSON blob with schema plus reviewer_weights activates
calibrated aggregation even though it has no source report hash, labeled-set
hash, pairwise dependency, derived-from-pairwise lineage, or calibration digest.

GREEN: no-lineage constant blobs return `None`, route through conservative
aggregation, and cannot become active calibrated weights.

## test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative

Public boundary: `run_dual_agent_workflow`

Maps to: Slice 3, P4

RED: a missing calibration path blocks or applies implicit default weights.

GREEN: route metadata marks calibration inactive and the conservative panel
decision advances clean accepts.

## test_run_dual_agent_workflow_calibrated_real_revise_still_blocks

Public boundary: `run_dual_agent_workflow`

Maps to: Slice 2, P3

RED: calibration averages an important reviewer revise into an accept.

GREEN: important revise still returns `blocking_reviewer_objection`, no
`calibrated_accept` payload is used, and the workflow blocks.

## Verification Commands

- `uv run pytest tests/test_reviewer_panel_eval_runner.py tests/test_reviewer_panel_aggregation.py tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_correlated_accept_escalates tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_independent_accept_advances tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_real_revise_still_blocks tests/test_dual_agent_workflow_driver.py::test_workflow_kwargs_from_payload_preserves_dynamic_workflow_preview_fields -q`
- `uv run pytest tests/test_reviewer_panel_eval_runner.py tests/test_dual_agent_workflow_driver.py -q`
- `uv run --extra dev pytest -q`
