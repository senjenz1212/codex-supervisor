# Issue Slices

## Slice 1: Supervisor-Built Review Packets

Priority: P1

Scope: implement the ReviewPacket schema/event, packet builder, packet
validator, and reviewer-context receipt check at the workflow review boundary.

PRD promises: P1, P2

Public boundary for first RED test: workflow review invocation/adjudication in
the supervisor driver, not a standalone helper.

Acceptance criteria:

- [ ] every configured reviewer invocation has a ReviewPacket event;
- [ ] packet includes planning refs, diff/name-status, changed file hashes,
  runtime-native receipt ids, declared tests, executed-test receipt ids, policy
  overlay/lesson hashes, and reviewer targets;
- [ ] packet hash is stable across identical inputs;
- [ ] packet validation fails when a git changed file is omitted;
- [ ] packet validation fails when a TDD acceptance item is omitted;
- [ ] reviewer context receipt missing changed files/criteria/receipts emits
  review_context_incomplete and cannot accept.

## Slice 2: Typed Worker Dispatch Ledger Events

Priority: P1

Scope: add ledger-backed worker/session/dispatch lifecycle events and link
read-only worker/reviewer outputs into EvidenceAttempt where appropriate.

PRD promises: P3

Public boundary for first RED test: dispatch/review paths that write supervisor
ledger events.

Acceptance criteria:

- [ ] roster/session/dispatch/completion/failure/block/cancel events are
  persisted with task_id, run_id, gate, purpose, provider_family, runtime,
  model, worktree or session refs, timings, cost, evidence_attempt_id, receipt
  ids, and transcript/output hashes;
- [ ] supervisor_worker_blocked and supervisor_worker_cancelled each have
  direct public-boundary tests with the same required field coverage as
  completed/failed events;
- [ ] read-only worker findings create or link EvidenceAttempt records;
- [ ] no filesystem registry is required to reconstruct dispatch history.

## Slice 3: Roster Preflight

Priority: P1

Scope: run worker/reviewer availability checks before fan-out or reviewer
routing and record explicit availability/degraded outcomes.

PRD promises: P4

Public boundary for first RED test: reviewer/worker routing preflight.

Acceptance criteria:

- [ ] roster preflight records available and unavailable workers/reviewers
  before routing;
- [ ] boot failure marks the backend unavailable for the run without
  retry-looping the same backend as a task failure;
- [ ] preflight runs before cross-vendor review selection.
- [ ] the preflight-before-selection ordering is asserted in a gate/routing
  test, not only inferred from helper call order.

## Slice 4: Cross-Vendor Review Gate Policy

Priority: P1

Scope: select a different reviewer provider_family from the implementation
provider where possible and make unavailable diversity explicit in gate policy.

PRD promises: P5, P2

Public boundary for first RED test: outcome/tdd/implementation review gate
adjudication with reviewer policy enabled.

Acceptance criteria:

- [ ] when implementation provider_family is known, a different
  provider_family is selected when available;
- [ ] when unavailable and reviewer_unavailable_policy=block, the gate blocks
  with degraded_review_unavailable or cross_vendor_review_unavailable;
- [ ] when unavailable and policy allows degraded review, an explicit degraded
  receipt/event is emitted;
- [ ] Cursor SDK rigorous review still runs on configured gates;
- [ ] Claude gate verdict remains required and separate.

## Coverage Index

- P1: Slice 1
- P2: Slices 1 and 4
- P3: Slice 2
- P4: Slice 3
- P5: Slice 4
