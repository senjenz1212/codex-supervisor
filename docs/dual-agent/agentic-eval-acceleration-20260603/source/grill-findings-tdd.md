# TDD Grill Findings: Agentic Eval Acceleration

Task id: `agentic-eval-acceleration-20260603`

### Finding 1: Tests Must Hit The Runner Boundary

Status: resolved

- Severity: critical
- Risk: helper-only tests could miss row/report export behavior.
- Resolution: every new behavioral test calls `agentic_eval_runner` or
  `build_agentic_eval_report` through the same row shape the runner emits.

### Finding 2: Qualification Needs A Full Truth Table

Status: resolved

- Severity: critical
- Risk: only testing the happy path could allow faster blocked or lower-quality
  arms to qualify.
- Resolution: add explicit predicate-failure cases for blocked status, lower
  score, higher missed issues, higher rejected gates, and insufficient speed.

### Finding 3: Stable Hash Should Cover The Parallel Corpus

Status: resolved

- Severity: important
- Risk: the corpus could drift without a visible report change.
- Resolution: assert a stable `report_sha256` for the accepted parallelism
  replay dataset and regenerate exports from that input.

### Finding 4: Report-Only Assertions Must Survive Recommendation Output

Status: resolved

- Severity: important
- Risk: adding a recommendation field could be treated as policy mutation.
- Resolution: extend the report-only test to check policy snapshot and mutation
  flags after recommendation generation.

### Finding 5: Latency Nulls Need Reasons

Status: resolved

- Severity: important
- Risk: absent timing receipts could be confused with zero time.
- Resolution: tests require `None` plus `not_recorded` for unavailable timing
  fields.
