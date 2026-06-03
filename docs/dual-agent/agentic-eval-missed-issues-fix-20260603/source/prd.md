# PRD: Agentic Eval Quality Signals From Evidence

Task id: `agentic-eval-missed-issues-fix-20260603`

## Problem Statement

The agentic eval runner scores required verdict evidence, but then lets
self-reported arm metrics override the derived quality counters. A recorded arm
can fail every required verdict and still report `missed_issues=0`, which masks
quality failure in the comparison report and makes fan-out acceleration data
untrustworthy.

The concrete regression is in the committed bridge report:
`clean-accept-runner-report` scored `0/5`, but the exported row showed
`missed_issues=0` because the recorded metrics object supplied that value.

## Solution

Make evidence-derived quality signals authoritative in
`supervisor.agentic_eval.agentic_eval_runner`. `missed_issues` must always come
from the evidence decision tree's `failed_verdict_count`; `rejected_gates` must
always come from workflow replay gate/probe outcomes. If self-reported metrics
conflict, preserve them as reported values and mark the row with a divergence
flag instead of letting them replace the authoritative fields. Speed and cost
remain metrics-sourced because the runner has no better source for those.

## User Stories

- As an operator, I can trust `missed_issues` to reflect failed required
  verdicts even when recorded metrics say otherwise.
- As a reviewer, I can see when recorded arm metrics disagree with replayed
  evidence and gate outcomes.
- As a maintainer, I can regenerate bridge reports and see corrected
  `missed_issues` values without changing policy defaults.

## PRD Promise Contracts

P1. Evidence-Derived Missed Issues

- Public boundary: `supervisor.agentic_eval.agentic_eval_runner`.
- Allowed outcomes: `row["missed_issues"]` equals the score object's
  `failed_verdict_count` for every arm.
- Forbidden outcomes: `arm["metrics"]["missed_issues"]` hides failed verdicts
  or replaces the derived count.

P2. Workflow-Derived Rejected Gates

- Public boundary: `supervisor.agentic_eval.agentic_eval_runner`.
- Allowed outcomes: `row["rejected_gates"]` equals the replay-derived
  `_rejected_gate_count(workflow_result)` for every arm.
- Forbidden outcomes: `arm["metrics"]["rejected_gates"]` hides rejected or
  blocked workflow gates/probes.

P3. Divergence Is Visible

- Public boundary: exported agentic eval rows and report JSON.
- Allowed outcomes: conflicting self-reported `missed_issues` and
  `rejected_gates` values are preserved under `reported_missed_issues` and
  `reported_rejected_gates`; `metrics_divergence` is true and names the
  divergent fields.
- Forbidden outcomes: conflicting metrics are dropped silently or overwrite the
  authoritative values.

P4. Speed And Cost Stay Metrics-Sourced

- Public boundary: `agentic_eval_runner` row construction.
- Allowed outcomes: `wall_clock_s` and `cost_usd` continue to use arm metrics.
- Forbidden outcomes: deriving speed/cost from quality evidence or changing
  acceleration semantics in this slice.

P5. Report-Only Regeneration

- Public boundary:
  `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/report/`.
- Allowed outcomes: regenerating the committed bridge report corrects
  `clean-accept-runner-report` so failed verdicts produce
  `missed_issues > 0` with divergence visible; `default_change_allowed` stays
  false and the policy snapshot stays off.
- Forbidden outcomes: changing `agentic_lead_policy`, enabling fan-out, touching
  `supervisor/state.py`, or mutating config defaults.

## Implementation Decisions

- Keep the public boundary in `agentic_eval_runner`; do not introduce a new
  runner or schema version for this bug fix.
- Add a small helper that compares metrics-provided quality fields to
  authoritative values and returns reported divergent fields.
- Preserve existing report shape for speed/cost and existing aggregation, but
  add `metrics_divergence` and `metrics_divergence_fields` to every row.
- Only add `reported_missed_issues` or `reported_rejected_gates` when the
  corresponding metric conflicts with the authoritative value.
- Regenerate only the report artifacts from the committed bridge dataset; do
  not rerun live workflow arms.

## Testing Decisions

- First RED tests must call `agentic_eval_runner`, not a helper, because row
  construction is the user-visible report boundary.
- Include one regression where all verdict evidence fails but
  `metrics.missed_issues` is zero.
- Include one regression where replayed gates are rejected but
  `metrics.rejected_gates` is zero.
- Include a non-divergent metrics case and a report-only invariant case.
- Rerun the bridge dataset in fixture replay and verify the corrected
  `clean-accept-runner-report` rows.

## Out Of Scope

- Acceleration ratio, speed win conditions, or policy enablement.
- Changing `agentic_lead_policy`, worker fan-out caps, or config defaults.
- Touching `supervisor/state.py` or scaling/transport layers.
- Rerunning external model providers for the bridge sample.
