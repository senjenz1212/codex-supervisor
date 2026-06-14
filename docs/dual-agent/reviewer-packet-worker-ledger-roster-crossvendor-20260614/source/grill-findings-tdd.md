# TDD Grill Findings

## Findings

### Finding T1: Helper-Only Packet Tests Would Miss Gate Integration

Status: resolved

- Risk: testing only a pure packet object would not prove workflow reviewers
  receive it or that incomplete context blocks acceptance.
- Resolution: RED 1 and RED 4 exercise workflow review invocation and gate
  adjudication.

### Finding T2: Cross-Vendor Policy Needs Negative Tests

Status: resolved

- Risk: only testing the happy path would permit silent same-vendor fallback.
- Resolution: RED 11 and RED 12 cover different-provider selection and explicit
  unavailable behavior.

### Finding T3: Runtime Receipts Must Stay Supervisor-Originated

Status: resolved

- Risk: agent-stamped runtime_native receipts could satisfy review packet
  validation.
- Resolution: RED 1 and RED 4 require runtime receipt ids from supervisor
  runtime evidence for the current gate.

### Finding T4: Reviewer Context Receipt Must Be Checked Against Packet, Not Free Text

Status: resolved

- Risk: reviewer could claim "reviewed all files" in prose without structured
  coverage.
- Resolution: RED 4 checks files reviewed, criteria checked, receipts
  considered, assumptions, and missing context against packet requirements.

### Finding T5: Roster Preflight Must Distinguish Boot Failure From Task Failure

Status: resolved

- Risk: an unavailable backend could be retried as a failed task and hide
  routing degradation.
- Resolution: RED 9, RED 10, and the roster-before-cross-vendor ordering test
  record boot status, prevent retry-looping the same unavailable backend, and
  prove cross-vendor selection consumes preflight evidence.

### Finding T6: All Required Worker Lifecycle Event Kinds Need Direct Coverage

Status: resolved

- Risk: supervisor_worker_blocked and supervisor_worker_cancelled could be
  omitted or mislabeled while completed/failed tests still pass.
- Resolution: the TDD plan now includes direct public-boundary tests for both
  blocked and cancelled lifecycle events with required field coverage.

## Translation Audit

- Every PRD promise has an issue claimant.
- Every issue names a public-boundary RED test.
- First tests hit supervisor workflow, ledger, review, or routing boundaries
  before helper-only tests.
- All grill findings are resolved in the TDD plan.
- No findings are waived.
