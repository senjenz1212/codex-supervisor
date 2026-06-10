# PRD: Human-Approved AutoResearch Policy Evolution

## Problem Statement

AutoResearch can produce accepted report-only evidence, but the supervisor has no audited bridge from a clean accepted attempt to a concrete prompt or config proposal and then to an explicitly approved change. Operators need the learning loop to be useful without letting evaluator output mutate policy automatically or bypass gates.

## Solution

Add a policy-evolution layer that builds non-mutating proposals from accepted, no-gaming AutoResearch records. A proposal records evaluator evidence, k-trial stats, gaming flags, cost, affected gates, target path, candidate artifact ref, before hash, after hash, and unified diff. Thin supervisor API/MCP tools expose proposal creation, approval, denial, and rollback to operators; the underlying policy-evolution module remains the deterministic service implementation. A separate operator approval call verifies current bytes, applies exactly the candidate artifact, writes approval metadata, and stores a rollback pointer. Denial and rollback are explicit ledger events.

## User Stories

- As an operator, I can inspect a concrete proposal produced from accepted AutoResearch evidence.
- As a reviewer, I can verify trial metrics, costs, gaming flags, affected gates, and before/after hashes.
- As a maintainer, I can require approver and approval channel before any artifact changes.
- As a rollback owner, I can restore the previous prompt or config artifact from a durable pointer.

## PRD Promise Contracts

P1. Accepted Clean Attempts Create Proposals

- User-visible promise: accepted AutoResearch records with no gaming flags can create stability proposals.
- Representative action: build proposals from a report with one accepted evaluator-backed record.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: proposal event includes source attempt, evaluator evidence, k-trial stats, cost, affected gates, hashes, and diff.
- Forbidden outcomes: rejected or gaming-flagged records create applyable proposals; proposal creation mutates files.

P2. Approval Required Before Mutation

- User-visible promise: proposal creation is read-only and operator approval is mandatory before applying.
- Representative action: create a proposal and inspect the target artifact before approval.
- Public boundary: `supervisor_tool_api`.
- Allowed outcomes: target bytes remain unchanged; proposal says `requires_operator_approval=true` and `default_change_allowed=false`.
- Forbidden outcomes: AutoResearch report acceptance changes defaults or writes prompt/config artifacts automatically.

P3. Approval Applies Exactly Recorded Diff

- User-visible promise: approval applies only the candidate artifact whose hash is recorded in the proposal.
- Representative action: approve with approver and channel.
- Public boundary: `supervisor_tool_api`.
- Allowed outcomes: current hash matches before hash; target hash becomes after hash; approval event records approver, channel, hashes, and rollback pointer.
- Forbidden outcomes: approval applies stale, unrecorded, or mismatched bytes.

P4. Denial And Rollback Are Replayable

- User-visible promise: denial applies nothing, and rollback restores previous bytes from a pointer.
- Representative action: deny one proposal; approve and roll back another.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: denial and rollback events are present; rollback restored hash equals original before hash.
- Forbidden outcomes: denial mutates files; rollback depends on human memory.

P5. Gate Authority Is Unchanged

- User-visible promise: proposals, approvals, denials, and rollbacks do not advance gates or override typed outcomes.
- Representative action: inspect all policy-evolution payloads.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: payloads say gate, reviewer panel, and typed outcome authority are unchanged.
- Forbidden outcomes: a proposal counts as gate acceptance or reviewer-panel acceptance.

## Implementation Decisions

- Implement in `supervisor/autoresearch/policy_evolution.py`.
- Expose thin operator-facing tools through `CodexSupervisorMcpAPI` / `mcp_tools/codex_supervisor_stdio.py` for create, approve, deny, and rollback.
- Treat candidate artifacts as repo-relative, hash-pinned files listed in the accepted attempt `changed_files`.
- Use `State.write_event` for proposal, approval, denial, and rollback audit events.
- Store rollback backup bytes under `.handoff/policy-rollbacks`.
- Leave existing AutoResearch report-only payload semantics unchanged.

## Testing Decisions

- Test proposal creation, rejected/gaming filtering, approval, denial, and rollback at public boundaries.
- Use temporary prompt/config-like files and `State` to verify real bytes and ledger events.
- Negatively test missing operator identity and missing approval channel so approval, denial, and rollback cannot mutate or emit events without human approval provenance.
- Assert authority invariants and `default_change_allowed=false`.
- Run existing AutoResearch and workflow regression tests.

## Out Of Scope

- Automatic policy mutation.
- Semantic ranking or LLM judging of proposals.
- Gate or reviewer-panel changes.
- Arbitrary patch application beyond target/candidate artifact replacement.
