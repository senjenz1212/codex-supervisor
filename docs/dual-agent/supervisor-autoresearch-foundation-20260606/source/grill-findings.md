# PRD Grill Findings: Supervisor AutoResearch Foundation

## Reviewed Surfaces

- `supervisor/agentic_eval.py` report-only runner and no-live-call invariant.
- `supervisor/state.py` ledger event boundary.
- `supervisor/workflow_job_dispatcher.py` durable job and backpressure pattern.
- `supervisor/dual_agent_workflow.py` deliverable evidence gate.
- `supervisor/reviewer_registry.py` reviewer aggregation authority.
- Research references: Autoresearch mutable/evaluator split, AIDE and
  AI-Scientist style attempt search, and PaperBench/MLE-bench reproduction
  discipline.

### Finding 1: Report-only recommendation must not become a hidden policy flip

Status: resolved

The PRD now states that every report keeps `default_change_allowed=false`, live
execution is disabled by default, and validators cannot advance gates or mutate
policy. Tests will assert the invariant at the report boundary and runner
boundary.

### Finding 2: Mutable path validation needs conservative default immutables

Status: resolved

The PRD now requires caller-supplied immutable paths plus default immutable
authority surfaces: ledger/state code, gate logic, reviewer aggregation,
validation code, scorer fixtures, and production configuration. Attempts that
touch these surfaces must be rejected with gaming flags.

### Finding 3: Single-metric promotion invites evaluator gaming

Status: resolved

The PRD now requires repeated trials, median and IQR reduction, unstable-trial
flags, and independent validation output. No single scalar can promote an
attempt; the report can only recommend operator review.

### Finding 4: Filesystem artifacts must not become truth

Status: resolved

The PRD now explicitly keeps the ledger as truth and treats `.handoff` and
report files as artifacts. The implementation will write additive ledger events
through the existing event API instead of creating a parallel truth layer.
