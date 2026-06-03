# TDD Plan: Agentic Eval Quality Signals

## test_agentic_eval_runner_derives_missed_issues_from_verdicts

Maps to: P1, P3.

RED: create a replay dataset arm whose required verdict evidence fails while
`metrics.missed_issues` reports 0; the current runner returns 0 and hides the
failed verdicts.

GREEN: set `row["missed_issues"]` from `failed_verdict_count`, preserve the
conflicting metric as `reported_missed_issues`, and set
`metrics_divergence=True`.

## test_agentic_eval_runner_derives_rejected_gates_from_workflow

Maps to: P2, P3.

RED: create a replay arm with rejected workflow gates/probes while
`metrics.rejected_gates` reports 0; the current runner returns 0.

GREEN: set `row["rejected_gates"]` from replayed workflow outcomes, preserve
the conflicting metric as `reported_rejected_gates`, and include the field in
`metrics_divergence_fields`.

## test_agentic_eval_runner_does_not_flag_consistent_quality_metrics

Maps to: P3, P4.

RED: consistent metrics have no explicit behavior and can be confused with
overridden metrics.

GREEN: when reported quality metrics match authoritative values, keep
`metrics_divergence=False` and leave speed/cost sourced from metrics.

## test_agentic_eval_bridge_expected_accept_requires_terminal_accept

Maps to: P1, P5.

RED: the bridge regression proves score 0 but does not prove the corrected
missed issue count.

GREEN: assert each failed required verdict increments `missed_issues` even
when the recorded arm metrics report 0.

## Regeneration Check

Maps to: P5.

Run the fixture-replay bridge report export against
`docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/agentic_eval_three_arm_dataset.yaml`.

Assert the committed bridge report has corrected `missed_issues`,
divergence flags, `default_change_allowed=False`, and policy snapshot `off`.
