# Test Evidence: Reviewer Panel Calibrated Weighting

Task id: `reviewer-panel-calibrated-weighting-20260602`

## Calibration Evidence

- Calibration input:
  `docs/dual-agent/reviewer-panel-calibrated-weighting-20260602/source/reviewer-panel-calibration-input.json`
  - file sha256: `0bdfe3e7ec048945bdf22ecafba9e14aad8eecda3ad8cb70c042be9eb929db9c`
  - source: real supervised workflow transcript events, not synthetic fixture rows
  - selected labels after active-roster filter: `4` accept_allowed, `1` block_required
  - roster filter: `3` mixed-lineage tasks excluded because reviewer rows did not match the declared active reviewer roster
- Eval report:
  `docs/dual-agent/reviewer-panel-calibrated-weighting-20260602/source/calibration-eval/report.json`
  - report sha256: `8583f100e9cb6a6dc1f718b58bd46067cb4cf9bf842f852e58b7bc4e4a0d97c7`
  - file sha256: `37078a2b5cec1474599e843b1236637406cbafb2bef1d8da36dcf7502b257da7`
  - provenance: `10` auditable real reviewer outputs, `0` fixture rows, `20` transcript refs
  - row roster consistency: `10/10` rows match runtime, model, provider_family, lineage, tool_access, and assurance_grade for the declared reviewer roster
- Calibration artifact:
  `docs/dual-agent/reviewer-panel-calibrated-weighting-20260602/source/calibration-eval/reviewer-panel-calibration.json`
  - calibration sha256: `c633f2168fb8adf987cb606305ef9d6c936e9ff5bab48ffda4bebb3b49a8fee7`
  - reviewer roster sha256: `70b069cd061ead35c78ba3a50a603515c15fbd41cb6d8d37ce851436a7e60ecb`
  - file sha256: `e2cdc92e8e11365fcef33136f7d1c5d90ec5b2ef6e3880fa150d10387df9bcf0`
  - loader status: active for the measured reviewer roster; fixture-only, row/roster-mismatched, and active-roster-mismatched calibration are rejected/fall back
- Measured reviewer weights:
  - `independent-reviewer-0`: `1.0`
  - `independent-reviewer-1`: `1.0`
- Active accept formula recorded in artifact:
  `aggregate = sum(weight * confidence * severity_multiplier) / reviewer_count`

## Validation Commands

- `uv run pytest tests/test_reviewer_panel_aggregation.py tests/test_reviewer_panel_eval_runner.py tests/test_dual_agent_workflow_driver.py::test_codex_cli_reviewer_parses_typed_outcome_with_hashes tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_correlated_accept_escalates tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_independent_accept_advances tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_real_revise_still_blocks tests/test_dual_agent_workflow_driver.py::test_workflow_kwargs_from_payload_preserves_dynamic_workflow_preview_fields -q` -> `20 passed in 9.21s`
- `uv run pytest tests/test_cursor_agent.py::test_run_litellm_structured_calls_openai_schema_gateway tests/test_reviewer_panel_aggregation.py tests/test_reviewer_panel_eval_runner.py tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_correlated_accept_escalates tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_independent_accept_advances tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_calibrated_real_revise_still_blocks -q` -> `21 passed in 10.15s`
- `uv run pytest tests/test_reviewer_panel_eval_runner.py tests/test_reviewer_panel_aggregation.py tests/test_dual_agent_workflow_driver.py -q` -> `119 passed in 103.62s`
- `uv run --extra dev pytest -q` -> `644 passed in 142.68s`
- `git diff --check` -> passed
- Recovery fix after rerun10/rerun13 transport stalls: `tests/test_cursor_agent.py::test_run_litellm_structured_calls_openai_schema_gateway` now asserts the LiteLLM/OpenAI-compatible reviewer inherits the supervisor `timeout_s` on both the OpenAI-compatible client and the chat-completions request, preventing rigorous reviewer calls from hanging past the gate timeout.

## Acceptance Mapping

- Weights derive from measured panel-eval calibration artifact:
  `test_reviewer_panel_eval_runner_emits_calibration_artifact_when_requested`
  and `test_reviewer_panel_calibration_is_deterministic_and_data_derived`.
- Measured-correlated unanimous accept escalates:
  `test_run_dual_agent_workflow_calibrated_correlated_accept_escalates`.
- Effectively independent accept advances:
  `test_run_dual_agent_workflow_calibrated_independent_accept_advances`.
- Severity participates in active accept aggregation:
  `test_evaluate_reviewer_panel_calibrated_accept_applies_severity_multiplier`.
- No-lineage constants cannot activate calibrated weighting:
  `test_load_reviewer_panel_calibration_rejects_no_lineage_constants`.
- Fixture-only calibration cannot activate calibrated weighting:
  `test_load_reviewer_panel_calibration_rejects_fixture_only_provenance`.
- Formula-inconsistent lineaged weights cannot activate calibrated weighting:
  `test_load_reviewer_panel_calibration_rejects_formula_inconsistent_weights`.
- Partial calibration for the active reviewer set falls back to conservative:
  `test_evaluate_reviewer_panel_partial_calibration_falls_back_to_conservative`.
- Calibration for a different reviewer runtime/provider roster falls back to conservative:
  `test_evaluate_reviewer_panel_lineage_mismatch_falls_back_to_conservative`.
- Calibration rows whose runtime/model/provider/lineage/tool metadata do not match
  the declared reviewer roster cannot emit/load active calibration:
  `test_reviewer_panel_calibration_rejects_mixed_lineage_rows` and
  `test_load_reviewer_panel_calibration_rejects_mixed_lineage_provenance`.
- Real important revise still blocks:
  `test_run_dual_agent_workflow_calibrated_real_revise_still_blocks`.
- Missing calibration falls back to conservative behavior:
  `test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative`.
- Codex CLI reviewer closes stdin under noninteractive workflow transport:
  `test_codex_cli_reviewer_parses_typed_outcome_with_hashes`.
- LiteLLM/OpenAI-compatible reviewer is bounded by the supervisor gate timeout:
  `test_run_litellm_structured_calls_openai_schema_gateway`.
