# Cursor Review Reliability Issues

## Slice ISS-1: Cursor Contract Retry Boundary

Type: AFK
Priority: P0
Estimate: M
Scope: Add bounded corrective retries to `invoke_cursor_agent` when `evaluate_outcome_fidelity` reports a missing or malformed typed outcome.
PRD promise: P1
First public-boundary RED test: `test_invoke_cursor_agent_retries_missing_outcome_with_contract_packet`

Acceptance Criteria:
- [ ] A missing `<dual_agent_outcome>` causes a corrective retry before terminal failure.
- [ ] The retry prompt restates the exact typed outcome contract and critical-review fields.
- [ ] A valid retry response is evaluated normally and can accept or revise.
- [ ] Retry metadata is available for ledger/event payloads.

## Slice ISS-2: Typed Reviewer Infrastructure Failure

Type: AFK
Priority: P0
Estimate: M
Scope: Represent terminal Cursor contract failure as a deterministic infrastructure classification instead of an indistinct red review probe.
PRD promise: P2, P4
First public-boundary RED test: `test_cursor_contract_miss_returns_reviewer_infrastructure_unavailable`

Acceptance Criteria:
- [ ] Repeated malformed output returns reason `reviewer_contract_unmet` or `reviewer_infrastructure_unavailable`.
- [ ] No fabricated Cursor `Outcome` is created.
- [ ] The result records retry count, status, model/run ids when available, and transcript tail.
- [ ] The gate can distinguish infrastructure classification from a real quality rejection.

## Slice ISS-3: Gate Recovery Classification

Type: AFK
Priority: P0
Estimate: M
Scope: Update the workflow gate decision path to classify Cursor infrastructure failure separately from valid Cursor `revise` or `deny`.
PRD promise: P3, P4
First public-boundary RED test: `test_workflow_records_cursor_infrastructure_failure_without_counting_accept`

Acceptance Criteria:
- [ ] Infrastructure failure is never counted as Cursor accept.
- [ ] Valid Cursor `revise` or `deny` still blocks the gate.
- [ ] Recovery policy either proceeds only when explicitly permitted or escalates with a deterministic reason.
- [ ] Gate events and interaction messages name the infrastructure classification.

## Slice ISS-4: Durable Reviewer Ledger Evidence

Type: AFK
Priority: P1
Estimate: S
Scope: Persist Cursor verdict or infrastructure classification in existing `tri_agent_cursor_review` and interaction events before transcript export/read.
PRD promise: P5
First public-boundary RED test: `test_read_gate_transcript_preserves_persisted_cursor_infrastructure_verdict`

Acceptance Criteria:
- [ ] `tri_agent_cursor_review` contains the typed classification.
- [ ] `read_gate_transcript` reads the persisted event without needing live Cursor transport.
- [ ] A simulated transcript/export transport failure does not mutate the stored verdict.
- [ ] Existing valid Cursor review transcript fields remain available.

## Slice ISS-5: Regression Safety For Valid Reviews

Type: AFK
Priority: P1
Estimate: S
Scope: Keep current valid-review behavior unchanged while adding retry and infrastructure classification.
PRD promise: P1, P3, P4
First public-boundary RED test: `test_valid_cursor_revise_still_blocks_after_retry_hardening`

Acceptance Criteria:
- [ ] A valid Cursor accept is handled normally.
- [ ] A valid Cursor revise still blocks.
- [ ] Worktree-modification detection still overrides reviewer accept.
- [ ] No P1/P2/P3/P13/P14 gate semantics are weakened.
