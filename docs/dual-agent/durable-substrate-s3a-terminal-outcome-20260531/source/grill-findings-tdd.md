# TDD Grill Findings: Durable Substrate S3a

### Finding 1: First RED Must Hit The Public Poll Boundary

Status: resolved

The TDD plan starts with `poll_dual_agent_workflow_job`, not helper-only tests,
because reconnecting clients experience this feature through the supervisor
tool API.

### Finding 2: Legacy Compatibility Needs Its Own Test

Status: resolved

Ledger-first polling can accidentally break old file-only jobs. The TDD plan now
has a dedicated legacy fallback/backfill test in addition to the missing-file
ledger test.

### Finding 3: Discrepancy Needs Behavioral Proof

Status: resolved

The PRD promises ledger authority, but that is easy to assert only indirectly.
The TDD plan now includes a direct ledger-vs-cache mismatch test and requires a
typed discrepancy event.

### Finding 4: Atomicity Must Be Tested Below The Tool Boundary

Status: resolved

The tool boundary proves reconnect behavior; the state helper owns atomicity.
The TDD plan includes a state-level helper test to prevent future callers from
committing terminal status without outcome.

### Finding 5: Scope Guard Is Regression-Based

Status: resolved

S3a must not alter gate/reviewer semantics. The TDD plan keeps focused workflow
driver tests plus the full suite as the regression guard instead of adding
unrelated projection rewrites.

### Finding 6: Worker-Side Completion Needs Its Own RED

Status: resolved

Poll-boundary tests alone could pass by backfilling from `result.json`, leaving
the unpolled deletion window open. The TDD plan now includes
`test_workflow_cli_records_terminal_outcome_in_ledger`.

### Finding 7: Discrepancy Test Must Avoid Raw Byte Equality

Status: resolved

The TDD plan now requires canonical parsed/redacted comparison so normal JSON
serialization differences do not create false audit discrepancies.

### Finding 8: Matching Cache Must Not Create Audit Noise

Status: resolved

Claude's TDD review identified that mismatch testing alone does not protect the
canonicalization promise. The TDD plan now includes
`test_poll_dual_agent_workflow_job_does_not_emit_discrepancy_for_matching_cache`.

### Finding 9: Atomicity Requires Rollback Proof

Status: resolved

The state helper must prove that status, outcome fields, and terminal event are
one transaction. The TDD plan now includes an injected event-failure rollback
test.
