# Implementation Plan

Task id: `reviewer-panel-calibrated-weighting-20260602`

## Files / Modules To Touch

- `supervisor/reviewer_panel_eval.py`: emit deterministic calibration artifacts
  from measured eval reports.
- `supervisor/reviewer_registry.py`: load calibration artifacts and apply
  calibrated all-accept aggregation without weakening blocks.
- `mcp_tools/codex_supervisor_stdio.py`: thread calibration path through
  workflow execution, route metadata, detached submit, and panel evaluation.
- `mcp_tools/codex_supervisor_workflow_cli.py`: preserve the calibration path in
  CLI workflow payloads.
- `supervisor/config.py` and `config.example.yaml`: add optional
  `reviewer_panel_calibration_path`.
- `tests/test_reviewer_panel_eval_runner.py`: calibration artifact and
  deterministic derivation tests.
- `tests/test_dual_agent_workflow_driver.py`: public-boundary workflow tests for
  calibrated escalation, independent advance, fallback, and hard block.

## Implementation Steps

1. Add `build_reviewer_panel_calibration(report)` with schema
   `reviewer-panel-calibration/v1`.
2. Derive pairwise dependency from same provider family, measured failure
   overlap, and positive measured correlations. Reviewer weight is computed from
   the maximum measured dependency touching that reviewer.
3. Add `emit_calibration=True` to the eval runner so it writes
   `reviewer-panel-calibration.json` alongside the report.
4. Add `load_reviewer_panel_calibration(path)` and optional `calibration` input
   to `evaluate_reviewer_panel`; the loader only activates artifacts with eval
   lineage fields and a matching deterministic calibration digest.
5. Keep the existing conservative order: missing verdicts, real non-accepts,
   and low-confidence accept escalation happen before calibrated all-accept
   weighting. Calibration only changes all-accept decisions, and the active
   all-accept calculation includes measured dependency weights plus severity
   multipliers.
6. Thread `reviewer_panel_calibration_path` through config, workflow tool, CLI,
   and detached submit.
7. Run focused tests, full workflow-driver tests, and full suite before
   supervised workflow execution.

## Risks

- Over-escalation: mitigated by no-artifact conservative fallback and tests that
  effectively independent accepts advance.
- Block weakening: mitigated by hard-block tests under active calibration.
- Non-reproducible calibration: mitigated by stable JSON hashing and fixed eval
  input tests.

## Traceability

- P1 -> `test_reviewer_panel_eval_runner_emits_calibration_artifact_when_requested`
  and `test_load_reviewer_panel_calibration_rejects_no_lineage_constants`
- P2 -> `test_run_dual_agent_workflow_calibrated_correlated_accept_escalates`,
  `test_run_dual_agent_workflow_calibrated_independent_accept_advances`, and
  `test_evaluate_reviewer_panel_calibrated_accept_applies_severity_multiplier`
- P3 -> `test_run_dual_agent_workflow_calibrated_real_revise_still_blocks`
- P4 -> `test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative`
- P5 -> `test_reviewer_panel_calibration_is_deterministic_and_data_derived`
