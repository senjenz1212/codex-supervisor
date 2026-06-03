# Issues: Agentic Eval Quality Signals

## Slice 1: Authoritative Quality Row Fields

Priority: high

Scope: change `agentic_eval_runner` row construction so missed issues and
rejected gates are derived from scored evidence and replayed workflow outcomes.

Acceptance Criteria:

- [ ] `missed_issues` equals `failed_verdict_count` even when metrics report 0.
- [ ] `rejected_gates` equals the replay-derived gate/probe rejection count
  even when metrics report 0.
- [ ] `wall_clock_s` and `cost_usd` still come from metrics.

PRD promises: P1, P2, P4.

## Slice 2: Metrics Divergence Visibility

Priority: high

Scope: preserve conflicting self-reported quality metrics under explicit
reported fields and flag divergence on the row.

Acceptance Criteria:

- [ ] A conflicting `metrics.missed_issues` produces
  `reported_missed_issues` and `metrics_divergence=True`.
- [ ] A conflicting `metrics.rejected_gates` produces
  `reported_rejected_gates` and `metrics_divergence=True`.
- [ ] Consistent metrics do not set a divergence flag.

PRD promises: P3.

## Slice 3: Regenerate Bridge Report

Priority: high

Scope: rerun the agentic eval runner over the committed bridge dataset and
update the report artifacts with corrected quality signals.

Acceptance Criteria:

- [ ] `clean-accept-runner-report` rows now show `missed_issues > 0`.
- [ ] Divergence is visible where recorded metrics conflict.
- [ ] `default_change_allowed` remains false and policy snapshot remains off.

PRD promises: P5.

## Coverage Index

- P1 covered by Slice 1.
- P2 covered by Slice 1.
- P3 covered by Slice 2.
- P4 covered by Slice 1.
- P5 covered by Slice 3.
