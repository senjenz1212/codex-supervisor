# Implementation Plan: Agentic Eval-Harness Runner

Task id: `agentic-eval-harness-runner-20260603`

## Files / Modules To Touch

- `supervisor/agentic_eval.py`: add dataset loading, replay-shape validation,
  evidence scoring, report row materialization, and deterministic artifact
  export while preserving `build_agentic_eval_report`.
- `tests/test_agentic_eval.py`: extend the report-builder coverage with public
  runner-boundary tests for required modes, equal budgets, replay shape,
  deterministic scoring, evidence failure, report-only export, and no-live
  replay.
- `tests/fixtures/agentic_eval/three_arm_tasks.yaml`: add the deterministic
  two-task, three-arm replay fixture with budgets, workflow cassettes, P-probes,
  reviewer-panel decisions, metrics, and evidence.
- `docs/dual-agent/agentic-eval-harness-runner-20260603/source/agentic-eval/report.json`:
  generated comparison report.
- `docs/dual-agent/agentic-eval-harness-runner-20260603/source/agentic-eval/evidence.json`:
  generated per-verdict evidence artifact.
- `docs/dual-agent/agentic-eval-harness-runner-20260603/test-evidence.md`:
  command receipts, artifact hashes, and fixture comparison summary.

## Implementation Steps

1. Preserve the existing report-builder public contract.
   - Keep `default_change_allowed=false`.
   - Keep required modes as `lead_direct`, `agentic_allowed`, and
     `agentic_required`.
   - Add score and graceful-degradation summary fields without authorizing any
     policy change.
2. Add `agentic_eval_runner`.
   - Load JSON/YAML datasets.
   - Require exactly the three configured modes per task.
   - Validate equal per-task token and USD budgets before rows are created.
3. Add replay-shape validation.
   - Require the six supervised workflow gates.
   - Require P1/P2/P3 on all arms and P13/P14 on agentic arms.
   - Require reviewer-panel decision payloads in the replayed final gate.
4. Add deterministic scoring.
   - Normalize `required_verdicts`.
   - Accept only concrete `probe_receipt`, `artifact_path`, or `diff_hunk`
     evidence with passing status.
   - Fail missing evidence and compute a deterministic 0-5 score.
5. Add deterministic export.
   - Write `report.json`, `evidence.json`, `rows.jsonl`, and
     `replay-manifest.json`.
   - Include dataset/report/evidence hashes.
6. Verify and run the supervised workflow.
   - Run focused tests, py_compile, diff check, workflow-adjacent regressions,
     and the full suite.
   - Pass receipts and generated artifacts into `run_dual_agent_workflow`.

## Risks

- Replay can drift into loose assertions if cassette shape is not validated;
  mitigate by rejecting missing workflow gates, probes, or reviewer-panel
  decisions.
- Fan-out can look better if it receives more total compute; mitigate by
  asserting equal token and USD budgets per task before any report row exists.
- The eval scorer can blur into production review; mitigate by keeping scoring
  deterministic and local to fixture evidence, with no reviewer-panel calls.
- Report generation could become policy mutation; mitigate by asserting
  `default_change_allowed=false`, policy snapshot `off`, and no config writes.

## Traceability

- P1 -> `test_agentic_eval_runner_covers_required_modes`,
  `test_agentic_eval_runner_requires_gated_replay_shape`, and
  `test_agentic_eval_report_compares_required_modes`.
- P2 -> `test_agentic_eval_runner_enforces_equal_budget`.
- P3 -> `test_agentic_eval_replay_blocks_live_calls` and
  `test_agentic_eval_runner_requires_gated_replay_shape`.
- P4 -> `test_agentic_eval_decision_tree_is_deterministic` and
  `test_agentic_eval_requires_evidence_for_verdict`.
- P5 -> `test_agentic_eval_runner_is_report_only` and
  `test_agentic_eval_report_compares_required_modes`.
