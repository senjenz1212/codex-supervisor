# Implementation Plan: Agentic Eval Quality Signals

## Files / Modules To Touch

- `supervisor/agentic_eval.py`
- `tests/test_agentic_eval.py`
- `tests/test_agentic_eval_bridge.py`
- `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/report/report.json`
- `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/report/rows.jsonl`
- `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/report/replay-manifest.json`
- `docs/dual-agent/agentic-eval-bridge-20260603/test-evidence.md`

## Steps

1. Add RED tests at `agentic_eval_runner` for `missed_issues` and
   `rejected_gates` conflicts.
2. Add a small divergence helper in `supervisor/agentic_eval.py`.
3. Make `missed_issues` and `rejected_gates` use authoritative values from
   scoring and replay, not metrics.
4. Preserve conflicting metrics under `reported_*` fields and set
   `metrics_divergence` plus `metrics_divergence_fields`.
5. Replay the committed bridge dataset and update the report artifacts.
6. Run focused, related, compile, whitespace, and full test suites.

## Risks

- Aggregated summaries change because missed issues are no longer hidden.
- Historical workflow transcripts still mention the old bridge report hash; the
  corrected report artifacts and task evidence must make the new hash explicit.
- The fix must not change speed/cost semantics or policy defaults.

## Traceability

- P1: `test_agentic_eval_runner_derives_missed_issues_from_verdicts` and
  `test_agentic_eval_bridge_expected_accept_requires_terminal_accept`.
- P2: `test_agentic_eval_runner_derives_rejected_gates_from_workflow`.
- P3: `test_agentic_eval_runner_derives_missed_issues_from_verdicts`,
  `test_agentic_eval_runner_derives_rejected_gates_from_workflow`, and
  `test_agentic_eval_runner_does_not_flag_consistent_quality_metrics`.
- P4: speed/cost assertions in
  `test_agentic_eval_runner_derives_missed_issues_from_verdicts`.
- P5: regenerated bridge report and `test-evidence.md`.
