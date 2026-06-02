# Test Evidence: Reviewer Panel Eval Runner

## Validation Commands

- `uv run pytest tests/test_reviewer_panel_eval_runner.py -q` -> initial RED: 6 failures from missing `supervisor.reviewer_panel_eval`; final GREEN: 6 passed.
- `uv run pytest tests/test_agentic_eval.py tests/test_dual_agent_workflow_driver.py::test_workflow_exposes_independent_reviewer_results_and_dual_writes_events tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_blocks_important_reviewer_revise tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept -q` -> 4 passed.
- `python3 -m py_compile supervisor/reviewer_panel_eval.py tests/test_reviewer_panel_eval_runner.py supervisor/agentic_eval.py` -> passed.
- `uv run --extra dev pytest -q` -> 625 passed.

## Acceptance Mapping

- Labeled panel replay runner: covered by `test_reviewer_panel_eval_runner_validates_labeled_fixture_schema` and `test_reviewer_panel_eval_runner_records_all_reviewer_rows`.
- Per-reviewer false-accept, false-block, missing, cost, and latency metrics: covered by `test_reviewer_panel_eval_runner_computes_per_reviewer_metrics`.
- Pairwise agreement, overlap, Jaccard, contingency, and phi/zero-variance handling: covered by `test_reviewer_panel_eval_runner_computes_pairwise_dependency_metrics`.
- Deterministic replay/export and eval-observation ledger events: covered by `test_reviewer_panel_eval_runner_exports_replay_and_ledger_artifacts`.
- Lead-mode/fan-out eval remains distinct: covered by `test_reviewer_panel_eval_runner_is_distinct_from_agentic_eval_report` plus the focused `tests/test_agentic_eval.py` regression command.

## Reports-Only Guard

- New code lives in `supervisor/reviewer_panel_eval.py`; `supervisor/agentic_eval.py` remains unchanged.
- The runner defaults to `execution_mode="fixture_replay"` and rejects non-fixture execution modes.
- Report payloads set `policy_change_allowed=false` and `active_weight_changes=[]`.
- Ledger writes use kind `reviewer_panel_eval_observation`, not a gate result or reviewer decision event.
