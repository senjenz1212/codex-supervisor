# Issues: Agentic Eval-Harness Runner

## Slice 1: Load The Three-Arm Corpus

Priority: P1

Scope: Add dataset loading to `supervisor/agentic_eval.py` for YAML/JSON task
fixtures. Each task must contain exactly `lead_direct`, `agentic_allowed`, and
`agentic_required`, and the runner must assert equal total `token_budget` plus
`budget_usd_limit` before materializing comparison rows.

PRD promises: P1, P2, P3.

Acceptance Criteria:

- [ ] `agentic_eval_runner(dataset_path=fixture_path)` emits one row per
  required mode for each fixture task.
- [ ] A task with unequal budget tuples raises before rows or artifacts are
  returned.
- [ ] Default fixture replay does not call a supplied live `workflow_runner`.

## Slice 2: Validate Workflow-Shaped Replay

Priority: P1

Scope: Require replay cassettes to carry the real dual-agent workflow shape:
`prd_review`, `issues_review`, `tdd_review`, `implementation_plan`,
`execution`, and `outcome_review`, plus P-probe statuses and reviewer-panel
decisions. Agentic arms must include P13/P14 probe evidence.

PRD promises: P1, P3.

Acceptance Criteria:

- [ ] A missing required workflow gate fails replay validation.
- [ ] A missing required P-probe fails replay validation.
- [ ] Accepted rows expose gate statuses, probe statuses, and reviewer-panel
  decisions for audit.

## Slice 3: Score Required Verdicts With Evidence

Priority: P1

Scope: Add deterministic scoring over per-task `required_verdicts`. A verdict
passes only when it has concrete evidence of an allowed kind: `probe_receipt`,
`artifact_path`, or `diff_hunk`, with a passing status. Missing evidence must
lower the score and mark the verdict failed.

PRD promises: P4.

Acceptance Criteria:

- [ ] Identical evidence maps produce identical score payloads.
- [ ] Removing evidence for a required verdict marks that verdict failed.
- [ ] Score payloads include per-verdict evidence records and failure reasons.

## Slice 4: Preserve Report Builder Contract

Priority: P2

Scope: Keep `build_agentic_eval_report(rows)` as the aggregator while preserving
`default_change_allowed=false` and required modes. Add row/summary support for
score and graceful degradation without changing production policy.

PRD promises: P1, P5.

Acceptance Criteria:

- [ ] Existing report-builder tests still pass.
- [ ] Summary rows include wall-clock, cost, retries, rejected gates, missed
  issues, operator interventions, score, and graceful degradation.
- [ ] The default-change gate still requires operator review.

## Slice 5: Export Report And Evidence Artifacts

Priority: P2

Scope: When `output_dir` is provided, write deterministic `report.json`,
`evidence.json`, `rows.jsonl`, and `replay-manifest.json`. The exports must
include stable hashes and the report-only policy snapshot.

PRD promises: P3, P4, P5.

Acceptance Criteria:

- [ ] Exported report records `default_change_allowed=false`.
- [ ] Exported evidence contains one evidence record per task/mode row.
- [ ] Replay manifest records dataset, report, and evidence hashes.
- [ ] No config, policy, worker-cap, or production reviewer files are mutated by
  the runner.

## Coverage

- P1: Slices 1, 2, and 4.
- P2: Slice 1.
- P3: Slices 1, 2, and 5.
- P4: Slices 3 and 5.
- P5: Slices 4 and 5.
