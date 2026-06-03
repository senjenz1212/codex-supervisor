# PRD Grill Findings: Agentic Eval Quality Signals

### Finding 1: Quality Counters Must Have One Authority

status: resolved

The bug exists because the report has two authorities: evidence scoring and
self-reported arm metrics. The PRD now makes verdict/gate replay the sole
authority for `missed_issues` and `rejected_gates`; metrics are retained only as
reported comparison data when they diverge.

### Finding 2: Divergence Must Be Auditable, Not Merely Corrected

status: resolved

Replacing the reported value without preserving it would make fixture problems
hard to diagnose. The PRD now requires `reported_missed_issues`,
`reported_rejected_gates`, `metrics_divergence`, and a list of divergent fields
so stale or self-serving cassettes remain visible.

### Finding 3: Do Not Fold Acceleration Into This Fix

status: resolved

The user cares about acceleration, but this slice only repairs the quality
floor. Speed/cost stay metrics-sourced and policy remains report-only; win
conditions and acceleration ratios are intentionally left to the next slice.
