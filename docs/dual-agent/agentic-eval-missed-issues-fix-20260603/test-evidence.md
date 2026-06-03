# Test Evidence: Agentic Eval Quality Signals

Task id: `agentic-eval-missed-issues-fix-20260603`

## RED

- `uv run pytest tests/test_agentic_eval.py::test_agentic_eval_runner_derives_missed_issues_from_verdicts tests/test_agentic_eval.py::test_agentic_eval_runner_derives_rejected_gates_from_workflow tests/test_agentic_eval.py::test_agentic_eval_runner_does_not_flag_consistent_quality_metrics tests/test_agentic_eval_bridge.py::test_agentic_eval_bridge_expected_accept_requires_terminal_accept -q`
  - Result before implementation: `4 failed`.
  - Failure mode: `metrics.missed_issues=0` and `metrics.rejected_gates=0`
    overrode the evidence/replay-derived quality counts, and
    `metrics_divergence` was absent.

## GREEN

- `uv run pytest tests/test_agentic_eval.py::test_agentic_eval_runner_derives_missed_issues_from_verdicts tests/test_agentic_eval.py::test_agentic_eval_runner_derives_rejected_gates_from_workflow tests/test_agentic_eval.py::test_agentic_eval_runner_does_not_flag_consistent_quality_metrics tests/test_agentic_eval_bridge.py::test_agentic_eval_bridge_expected_accept_requires_terminal_accept -q`
  - Result: `4 passed`.
- `uv run pytest tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py -q`
  - Result: `19 passed`.
- `uv run python -m py_compile supervisor/agentic_eval.py tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py`
  - Result: passed.
- `uv run pytest tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py tests/test_agentic_eval_corpus.py tests/test_reviewer_panel_eval_runner.py -q`
  - Result: `37 passed`.
- `git diff --check`
  - Result: passed.
- `uv run --extra dev pytest -q`
  - Result: `671 passed in 156.23s`.

## Bridge Report Replay

Command:

`uv run python - <<'PY' ... agentic_eval_runner(dataset_path='docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/agentic_eval_three_arm_dataset.yaml', output_dir='docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/report') ... PY`

Result:

- Corrected report sha256:
  `02f9551e7f547c45c55f75e5df10eca409b4c64e51c4a2c1dafda62fe82af308`
- `clean-accept-runner-report` corrected rows:

| mode | score | missed_issues | reported_missed_issues | rejected_gates | metrics_divergence | metrics_divergence_fields |
|---|---:|---:|---:|---:|---|---|
| lead_direct | 0.0 | 2 | 0 | 2 | true | missed_issues |
| agentic_allowed | 0.0 | 2 | 0 | 2 | true | missed_issues |
| agentic_required | 0.0 | 2 | 0 | 2 | true | missed_issues |

## Report-Only Invariants

- `default_change_allowed`: false.
- `agentic_lead_policy_snapshot.policy`: `off`.
- `agentic_lead_policy_snapshot.mutated`: false.
- `report_only.policy_mutated`: false.
- `report_only.config_mutated`: false.
- No `supervisor/state.py`, config default, fan-out policy, or scaling files
  were edited.
