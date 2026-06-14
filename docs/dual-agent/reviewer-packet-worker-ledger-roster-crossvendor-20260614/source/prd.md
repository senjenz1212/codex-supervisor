# PRD: Reviewer Packet, Worker Ledger, Roster Preflight, Cross-Vendor Gate

## Intent

Adopt the useful Omnigent/Polly orchestration patterns while preserving the
codex-supervisor truth model: supervisor ledger events, runtime-native
receipts, typed outcomes, and deterministic gate policy remain authoritative.
Shared files, implementer claims, prompts, and transcripts remain artifacts.

## Problem Statement

The supervisor already has runtime evidence floors, Cursor SDK review, Claude
gate review, worker receipts, and EvidenceAttempt foundations. The next gap is
that reviewer context and worker routing are still too implicit:

- reviewers may see free-form context rather than a supervisor-built packet;
- worker dispatch lifecycle is not consistently typed in the ledger;
- roster availability is not preflighted before choosing reviewers/workers;
- cross-vendor review is policy intent, but not yet a deterministic gate
  contract with explicit degraded/blocking outcomes.

## Solution

Add a supervisor-built ReviewPacket for each reviewer invocation, persist typed
worker dispatch lifecycle events, run roster preflight before reviewer/worker
routing, and enforce cross-vendor review policy with explicit degraded or
blocking outcomes.

## User Stories

1. As an operator, I can replay exactly what context a reviewer received.
2. As an operator, I can distinguish worker boot failure from task failure.
3. As an operator, I can prove that cross-vendor review was selected or
   explicitly unavailable.
4. As a reviewer, I receive a clean supervisor packet with diff, requirements,
   receipts, and changed-file hashes rather than implementer narrative.

## Goals

1. Build reviewer context in-process from supervisor state and runtime evidence.
2. Persist typed worker dispatch lifecycle events in the ledger.
3. Run roster preflight before fan-out or reviewer routing.
4. Enforce cross-vendor review policy where implementation vendor is known.
5. Preserve all existing gate semantics, runtime floors, reviewer authority, and
   ledger-as-truth invariants.

## Implementation Decisions

- ReviewPacket is ledger-backed and hash-stable.
- Worker dispatch facts are ledger events; filesystem outputs are artifact refs.
- Roster preflight runs before cross-vendor selection.
- Cross-vendor unavailability is explicit: block when policy is block, otherwise
  emit degraded review evidence.
- Reviewer context receipt is validated against the packet contract.

## Testing Decisions

- Public-boundary tests exercise workflow review invocation, ledger writes,
  roster routing, and gate adjudication before helper-only tests.
- Negative tests cover omitted changed files, omitted acceptance criteria,
  missing reviewer context, unavailable provider diversity, and missing Claude
  verdict.
- Regression tests preserve runtime-native evidence, Cursor SDK review, Claude
  gate verdicts, and P1/P2/P3/P11/P12/P13/P14 semantics.

## Out Of Scope

- Copying Omnigent/Polly's filesystem registry model.
- Making shared files, prompts, transcripts, or worker claims truth.
- Changing the existing gate sequence or P1/P2/P3/P11/P12/P13/P14 semantics.
- Allowing validators or reviewers to advance gates directly.
- Disabling Cursor SDK rigorous review or Claude gate review.

## PRD Promise Contracts

P1. Supervisor-Built Review Packet
P2. Reviewer Context Receipt
P3. Typed Worker Dispatch Ledger
P4. Roster Preflight
P5. Cross-Vendor Review Gate Policy

### P1. Supervisor-Built Review Packet

- User-visible promise: every reviewer invocation receives a deterministic
  ReviewPacket built by the supervisor, not an implementer-authored narrative.
- Public boundary: workflow review invocation and ledger event export.
- Required data: task_id, run_id, gate, packet_id, base_head, candidate_head or
  patch_hash, PRD/TDD/acceptance refs with hashes, git diff/name-status refs
  with hashes, changed file hashes, runtime-native receipt ids, declared tests,
  supervisor executed-test receipt ids, dependency/context refs, known lesson and
  policy overlay hashes, reviewer ids, packet_sha256.
- Allowed outcome: packet is stable, hashable, replayable, and recorded.
- Forbidden outcome: reviewer acceptance based only on implementer prose or a
  packet missing changed files, acceptance items, runtime receipts, or declared
  tests.

### P2. Reviewer Context Receipt

- User-visible promise: a reviewer must report which packet context it actually
  inspected before its verdict can satisfy the gate.
- Public boundary: Cursor/independent review result normalization and gate
  adjudication.
- Required data: files reviewed, criteria checked, receipts considered,
  assumptions, missing context.
- Allowed outcome: complete receipt allows normal gate adjudication.
- Forbidden outcome: missing changed file, omitted acceptance criterion, or
  missing runtime receipt coverage silently accepts.

### P3. Typed Worker Dispatch Ledger

- User-visible promise: worker and reviewer lifecycles are replayable from the
  ledger, not inferred from scratch files.
- Public boundary: worker/reviewer dispatch and completion paths.
- Required events: supervisor_worker_roster_checked,
  supervisor_worker_session_created, supervisor_worker_dispatched,
  supervisor_worker_completed, supervisor_worker_failed,
  supervisor_worker_blocked, supervisor_worker_cancelled.
- Required fields: task_id, run_id, gate, worker_id or reviewer_id, purpose,
  provider_family, runtime, model, worktree_ref, branch, session_id or
  conversation_id, status, timestamps, cost_usd, wall_clock_s,
  evidence_attempt_id, receipt_ids, output/transcript refs and hashes.
- Forbidden outcome: .polly/registry.json-style filesystem state is treated as
  authoritative.

### P4. Roster Preflight

- User-visible promise: supervisor records which workers/reviewers are available
  before routing work.
- Public boundary: fan-out/reviewer selection.
- Required data: runtime, command/provider, model availability, boot status,
  failure reason, policy outcome.
- Allowed outcome: boot failures mark the backend unavailable for this run.
- Forbidden outcome: unavailable backend is retried as if it were a task failure,
  or fake diversity is recorded.

### P5. Cross-Vendor Review Gate Policy

- User-visible promise: when implementation vendor is known and policy requires
  diversity, review uses a different provider_family where available.
- Public boundary: gate adjudication on configured review gates.
- Allowed outcome: different provider family selected, or explicit
  degraded_review_unavailable/block according to reviewer_unavailable_policy.
- Forbidden outcome: same-vendor review silently satisfies cross-vendor policy.

## Invariants

- Ledger remains truth; files are artifacts.
- Runtime-native evidence must originate from supervisor runtime evidence
  collection for the current gate invocation.
- Cursor SDK rigorous review remains enabled on configured gates.
- Claude gate verdict remains required.
- Existing agent-supplied receipts remain evidence only; they cannot satisfy the
  runtime-native floor.
