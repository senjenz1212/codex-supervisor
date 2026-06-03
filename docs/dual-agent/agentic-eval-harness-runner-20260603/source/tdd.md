# TDD Plan: Agentic Eval-Harness Runner

## RED / GREEN Strategy

Red: add public-boundary tests against `agentic_eval_runner` and
`score_agentic_eval_arm` before implementation so the first failures prove the
runner, replay, budget, scoring, and report-only contracts are absent.

Green: implement only `supervisor/agentic_eval.py`, the fixture dataset, and the
focused test file until every RED test passes; then run workflow-adjacent and
full-suite regressions before the supervised outcome review.

## test_agentic_eval_runner_covers_required_modes

Maps to: P1, P2, Slice 1, Slice 4.

- Boundary: `agentic_eval_runner(dataset_path=fixture_path)`.
- Red: runner does not exist or fails to emit one row per required mode.
- Green: every fixture task produces `lead_direct`, `agentic_allowed`, and
  `agentic_required` rows with accepted workflow status, gate statuses, P-probe
  statuses, reviewer-panel decisions, and equal budget fields.

## test_agentic_eval_runner_enforces_equal_budget

Maps to: P2, Slice 1.

- Boundary: `agentic_eval_runner(dataset_path=tmp_fixture)`.
- Red: an unequal `token_budget` or `budget_usd_limit` silently reaches report
  aggregation.
- Green: unequal arm budgets raise `ValueError` before rows or artifacts are
  materialized.

## test_agentic_eval_runner_requires_gated_replay_shape

Maps to: P1, P3, Slice 2.

- Boundary: `agentic_eval_runner(dataset_path=tmp_fixture)`.
- Red: a cassette missing a required P-probe still produces a comparison row.
- Green: replay validation rejects missing workflow gate/probe/panel structure
  before scoring.

## test_agentic_eval_decision_tree_is_deterministic

Maps to: P4, Slice 3.

- Boundary: `score_agentic_eval_arm`.
- Red: scoring depends on mutable process state or holistic judgment.
- Green: identical task, arm, evidence, and workflow input produces identical
  score payloads and a deterministic 0-5 score.

## test_agentic_eval_requires_evidence_for_verdict

Maps to: P4, Slice 3.

- Boundary: `score_agentic_eval_arm`.
- Red: a required verdict with no evidence still receives passing credit.
- Green: missing evidence marks the verdict failed, records
  `missing_evidence`, and lowers the score.

## test_agentic_eval_runner_is_report_only

Maps to: P5, Slice 5.

- Boundary: `agentic_eval_runner(..., output_dir=...)`.
- Red: report export is absent or lacks the no-default-change fields.
- Green: report/evidence/rows/manifest artifacts are written, and report fields
  keep `default_change_allowed=false`, `policy_mutated=false`, and
  `agentic_lead_policy_snapshot.policy=off`.

## test_agentic_eval_replay_blocks_live_calls

Maps to: P3, Slice 1.

- Boundary: `agentic_eval_runner`.
- Red: default fixture replay invokes a supplied live workflow runner.
- Green: fixture replay never invokes the runner, while non-fixture execution
  raises unless `allow_live_calls=True`.

## test_agentic_eval_report_compares_required_modes

Maps to: P1, P5, Slice 4.

- Boundary: `build_agentic_eval_report`.
- Red: existing report-builder behavior regresses while adding the runner.
- Green: required modes and `default_change_allowed=false` remain unchanged,
  with added score/graceful-degradation aggregation.

## Regression Commands

- `uv run pytest tests/test_agentic_eval.py -q`
- `uv run python -m py_compile supervisor/agentic_eval.py tests/test_agentic_eval.py`
- `git diff --check`
- `uv run pytest tests/test_agentic_eval.py tests/test_agentic_executor.py tests/test_dual_agent_workflow_driver.py -q`
- `uv run --extra dev pytest -q`
