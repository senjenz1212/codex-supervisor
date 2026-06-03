# PRD Grill Findings: Agentic Eval Trials And Strata

Task id: `agentic-eval-trials-strata-20260603`

### Finding 1: Trial reduction must happen before acceleration

Status: resolved

If acceleration is computed per trial and then summarized, a fast outlier can
distort the decision. The PRD now requires each task/mode to reduce repeated
trials to median wall-clock first, then compute acceleration and qualification
from the reduced row.

### Finding 2: Quality cannot be averaged across trials

Status: resolved

Scores and missed-issue counts are quality floors, not speed metrics. The PRD
now forbids averaging quality and requires an explicit
`quality_unstable_across_trials` flag when trials disagree.

### Finding 3: Pooled recommendations can hide class regressions

Status: resolved

A broad archaeology task and a narrow single-file task should not share one
verdict surface. The PRD now makes task-class summaries the recommendation
input and forbids pooled class decisions.

### Finding 4: Small-sample p95 is misleading

Status: resolved

The PRD now gates `acceleration_ratio_p95` on at least twenty tasks per class
and requires a machine-readable unavailable reason when suppressed.
