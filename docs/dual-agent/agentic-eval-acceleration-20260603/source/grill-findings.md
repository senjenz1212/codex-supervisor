# PRD Grill Findings: Agentic Eval Acceleration

Task id: `agentic-eval-acceleration-20260603`

### Finding 1: Faster Blocked Runs Must Not Count

Status: resolved

- Severity: critical
- Risk: the bridge dataset had all arms blocked, so a naive ratio would reward
  orchestration failure.
- Resolution: P2 requires `workflow_status == "accepted"` before a mode can
  qualify. Tests must include a faster blocked arm that fails qualification.

### Finding 2: Speed Cannot Override Verdict-Derived Quality

Status: resolved

- Severity: critical
- Risk: acceleration could reintroduce the masking fixed by the prior slice if
  it trusts self-reported `missed_issues` or `rejected_gates`.
- Resolution: P2 explicitly uses evidence-derived score, missed issues, and
  rejected gates already present in runner rows.

### Finding 3: Timing Evidence May Be Incomplete

Status: resolved

- Severity: important
- Risk: latency breakdown fields invite fabricated precision when cassettes do
  not contain enough receipts.
- Resolution: P3 requires `null` plus an unavailable reason for each missing
  timing component.

### Finding 4: Recommendation Must Stay Report-Only

Status: resolved

- Severity: important
- Risk: an acceleration report could be mistaken for authorization to flip
  policy.
- Resolution: P5 preserves `default_change_allowed=false`, policy snapshot
  `off`, and a recommendation-only payload.

### Finding 5: Corpus Needs Accepted Parallel Work

Status: resolved

- Severity: important
- Risk: measuring only the committed bridge sample would prove only blocked-run
  overhead.
- Resolution: P4 requires at least two accepted, parallelism-friendly replay
  cases in the eval corpus and exported artifacts.
