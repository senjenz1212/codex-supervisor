# Implementation Plan: Reviewer Panel Eval Runner

Task id: `reviewer-panel-eval-runner-20260601`

## Summary

Add a reports-only reviewer-panel eval runner under a new
`reviewer_panel_eval_runner` public boundary. The runner replays deterministic
labeled fixtures through the configured reviewer roster, records one row per
reviewer per task, computes per-reviewer and pairwise dependency metrics, and
exports replayable evidence without changing gate aggregation or policy.

## Files / Modules To Touch

- `docs/testing/public-boundaries.md`
- `supervisor/reviewer_panel_eval.py`
- `tests/test_reviewer_panel_eval_runner.py`
- `docs/dual-agent/reviewer-panel-eval-runner-20260601/test-evidence.md`
- `docs/dual-agent/reviewer-panel-eval-runner-20260601/workflow-request-cli.json`

## Steps

1. Register `reviewer_panel_eval_runner` in the public-boundaries document so
   tests start at the intended operator boundary.
2. Add a new `supervisor.reviewer_panel_eval` module instead of extending
   `supervisor.agentic_eval`, preserving the lead-mode/fan-out eval track.
3. Implement fixture-schema validation with labels `accept_allowed` and
   `block_required`, defaulting to `execution_mode=fixture_replay` and rejecting
   non-fixture execution modes.
4. Normalize reviewer roster specs and replay cassettes into deterministic rows
   sorted by task id and reviewer id, including explicit missing/unavailable
   rows when a verdict is absent.
5. Compute per-reviewer metrics: decision counts, verdict-present count,
   false-accept/false-block rates with denominators, false-block causes,
   unavailable rate, cost totals/averages, and latency totals/averages.
6. Compute pairwise metrics from per-reviewer rows: agreement/disagreement,
   false-accept and false-block overlap, combined failure Jaccard, contingency
   tables, and phi correlation with `not_applicable` on zero variance.
7. Export report JSON, markdown, raw rows, and replay manifest; optionally write
   ledger events as `reviewer_panel_eval_observation` only.
8. Keep `supervisor.agentic_eval.build_agentic_eval_report` unchanged and prove
   the schemas remain distinct.

## Risks

- Pairwise metrics can accidentally imply policy weighting. The report must
  keep `policy_change_allowed=false`, emit no active weights, and state that it
  is measurement-only.
- Missing reviewer verdicts can be misread as accepts. The row normalization
  and per-reviewer metrics must preserve `verdict_present=false`, decision
  `missing`, and missing/unavailable false-block causes.
- Correlation math can overstate small or zero-variance samples. Phi
  correlation must include raw contingency counts and return
  `not_applicable` with a reason when variance is zero.
- Extending the existing lead-mode eval would conflate two tracks. The new
  module and distinction regression keep the reviewer-panel runner separate.

## Traceability

- P1 -> `test_reviewer_panel_eval_runner_validates_labeled_fixture_schema`,
  `test_reviewer_panel_eval_runner_records_all_reviewer_rows`
- P2 -> `test_reviewer_panel_eval_runner_computes_per_reviewer_metrics`
- P3 -> `test_reviewer_panel_eval_runner_computes_pairwise_dependency_metrics`
- P4 -> `test_reviewer_panel_eval_runner_records_all_reviewer_rows`,
  `test_reviewer_panel_eval_runner_exports_replay_and_ledger_artifacts`
- P5 -> `test_reviewer_panel_eval_runner_exports_replay_and_ledger_artifacts`
- P6 -> `test_reviewer_panel_eval_runner_is_distinct_from_agentic_eval_report`
