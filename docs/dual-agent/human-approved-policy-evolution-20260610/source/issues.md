# Issues

## Slice 1: Build AutoResearch Policy Proposals

Priority: high
Estimate: medium
Scope: Add a deterministic proposal builder and proposal-created ledger event for accepted clean AutoResearch records.
PRD promises: P1, P2, P5.
Public boundary: `supervisor_event_ledger`.

Acceptance criteria:

- [ ] Accepted evaluator-backed record with no gaming flags creates one proposed payload.
- [ ] Proposal includes evaluator evidence, k-trial stats, cost, affected gates, hashes, and diff.
- [ ] Proposal creation leaves target prompt/config bytes unchanged.

## Slice 2: Block Rejected Or Gaming-Flagged Attempts

Priority: high
Estimate: small
Scope: Reject proposal creation for records that are rejected or carry any gaming flag.
PRD promises: P1, P2.
Public boundary: `supervisor_event_ledger`.

Acceptance criteria:

- [ ] Rejected records create no applyable proposals.
- [ ] Accepted records with gaming flags create no applyable proposals.
- [ ] No mutation occurs while filtering.

## Slice 3: Approve, Deny, And Audit

Priority: high
Estimate: medium
Scope: Add explicit operator approval and denial helpers plus thin supervisor API/MCP tools with ledger events.
PRD promises: P2, P3, P4, P5.
Public boundary: `supervisor_tool_api`.

Acceptance criteria:

- [ ] Approval verifies before hash and candidate hash, applies exact bytes, and records approver/channel/hashes.
- [ ] Approval and denial reject blank approver or blank approval channel before mutation and before audit events.
- [ ] Denial records approver/channel/reason and applies nothing.
- [ ] Approval and denial events preserve gate-authority invariants.
- [ ] Supervisor API/MCP tools drive proposal creation, approval, denial, and rollback without direct module imports.

## Slice 4: Rollback

Priority: high
Estimate: small
Scope: Restore previous bytes through the approval-recorded rollback pointer.
PRD promises: P4.
Public boundary: `supervisor_tool_api`.

Acceptance criteria:

- [ ] Rollback restores bytes whose hash equals the recorded before hash.
- [ ] Rollback writes a ledger event with restored hashes.
- [ ] Rollback rejects blank approver or blank approval channel before mutation and before audit events.
- [ ] Rollback preserves gate, reviewer-panel, typed-outcome, and default-change authority invariants.

## Slice 5: Regression Coverage

Priority: medium
Estimate: small
Scope: Keep existing AutoResearch report-only and supervisor workflow tests green.
PRD promises: P1-P5.
Public boundary: `agentic_eval_report`.

Acceptance criteria:

- [ ] AutoResearch report-only tests still pass.
- [ ] Stability proposal tests still pass.
- [ ] Workflow and full-suite tests remain green.
