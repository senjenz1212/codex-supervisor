# TDD Plan

## Test Strategy

Start with public-boundary tests that exercise the supervisor workflow,
reviewer routing, ledger writes, and gate adjudication. Helper-level tests come
after the public boundary proves the promise.

## test_review_packet_event_created_for_reviewer_invocation

Maps to: Slice 1, P1, P2

Red: a workflow review invocation emits no ReviewPacket event and reviewer
context is still assembled as legacy prompt text.

Green: packet event includes PRD/TDD refs, acceptance items, diff/name-status
refs, changed file hashes, runtime-native receipt ids, declared tests,
executed-test receipt ids, policy/lesson hashes, reviewer ids, and stable
packet_sha256.

## test_review_packet_validation_rejects_missing_changed_file

Maps to: Slice 1, P1

Red: git name-status contains supervisor/example.py but packet validation
accepts a packet that omits it.

Green: validation emits review_packet_changed_file_missing and gate cannot
accept.

## test_review_packet_validation_rejects_missing_acceptance_item

Maps to: Slice 1, P1

Red: a TDD acceptance item is not represented in packet criteria but the packet
still validates.

Green: validation emits review_packet_acceptance_item_missing and gate cannot
accept.

## test_reviewer_context_receipt_missing_changed_file_blocks_gate

Maps to: Slice 1, Slice 4, P2

Red: reviewer outcome accepts while context receipt omits a changed file or
runtime receipt.

Green: gate emits review_context_incomplete and does not accept.

## test_review_packet_excludes_implementer_transcript_by_default

Maps to: Slice 1, P1

Red: implementer transcript appears in the default reviewer packet as evidence.

Green: default packet excludes implementer transcript unless an explicit
supervisor-owned dependency/context ref is included.

## test_reviewer_packet_exposes_read_only_changed_file_contents

Maps to: Slice 1, P1

Red: reviewer cannot inspect changed file contents or packet hash does not
match the post-change file.

Green: reviewer context can inspect content through a read-only packet/worktree
and the hash matches the packet.

## test_worker_dispatch_events_persist_typed_lifecycle

Maps to: Slice 2, P3

Red: read-only worker/reviewer dispatch leaves only scratch artifacts with no
typed lifecycle events.

Green: ledger records session_created, dispatched, completed, and failed events
with purpose, provider_family, runtime, model, refs, timings, cost,
evidence_attempt_id, receipt ids, and transcript/output hashes.

## test_worker_blocked_event_persists_required_fields

Maps to: Slice 2, P3

Red: a blocked worker path records only a generic failure or scratch artifact
instead of supervisor_worker_blocked.

Green: supervisor_worker_blocked is persisted with task_id, run_id, gate,
worker_id, purpose, provider_family, runtime, model, status, timing/cost fields,
evidence_attempt_id, receipt ids, and output/transcript refs where available.

## test_worker_cancelled_event_persists_required_fields

Maps to: Slice 2, P3

Red: a cancelled worker path records no lifecycle event, or records a generic
failure that cannot be distinguished from task failure.

Green: supervisor_worker_cancelled is persisted with task_id, run_id, gate,
worker_id, purpose, provider_family, runtime, model, status, timing/cost fields,
evidence_attempt_id, receipt ids, and cancellation reason.

## test_read_only_worker_finding_links_evidence_attempt

Maps to: Slice 2, P3

Red: read-only worker finding receipt is not represented as an EvidenceAttempt
or linked dispatch event.

Green: a ledger-backed EvidenceAttempt exists or is linked with the worker
dispatch event.

## test_roster_preflight_records_available_and_unavailable_workers

Maps to: Slice 3, P4

Red: routing proceeds without a roster preflight event.

Green: supervisor_worker_roster_checked records available and unavailable
workers/reviewers with boot status and failure reason.

## test_roster_preflight_happens_before_cross_vendor_selection

Maps to: Slice 3, Slice 4, P4, P5

Red: cross-vendor reviewer selection can run before roster availability is
recorded, making degraded_review_unavailable or block decisions unauditable.

Green: ledger ordering shows supervisor_worker_roster_checked before
cross-vendor selection events, and selection consumes that preflight result.

## test_roster_boot_failure_marks_backend_unavailable_without_retry_loop

Maps to: Slice 3, P4

Red: a backend boot failure is retried as a task failure loop.

Green: backend is unavailable for the run and no repeated task retry loop is
attempted against it.

## test_cross_vendor_review_selects_different_provider_family

Maps to: Slice 4, P5

Red: implementation provider_family=anthropic and same-provider reviewer
silently satisfies cross-vendor policy despite an alternate reviewer.

Green: selected reviewer provider_family differs from implementation
provider_family and event records the selection.

## test_cross_vendor_unavailable_blocks_or_degrades_explicitly

Maps to: Slice 4, P5

Red: only same-provider reviewer is available and diversity is silently treated
as satisfied.

Green: block policy blocks; allow/escalate policy emits explicit
degraded_review_unavailable evidence and never silently accepts diversity.

## test_cursor_sdk_rigorous_review_still_runs_on_configured_gates

Maps to: Slice 4, P2, P5

Red: cross-vendor policy suppresses or replaces Cursor SDK rigorous review.

Green: Cursor SDK review events still exist and accepted/minor-only reviews are
required according to existing policy.

## test_claude_gate_verdict_remains_required

Maps to: Slice 4, P2, P5

Red: Cursor accepts while Claude gate outcome is missing or revise and the gate
still accepts.

Green: missing/revise Claude verdict blocks as before.

## Regression Suite

Run focused tests for:

- reviewer packet and context validation;
- worker dispatch ledger events;
- roster preflight;
- cross-vendor gate policy;
- existing workflow driver, Cursor reviewer, runtime evidence, EvidenceAttempt,
  and reviewer panel tests touched by this slice.

Full acceptance requires the focused suite green and no weakening of P1/P2/P3,
P11/P12/P13/P14, runtime evidence, or reviewer panel semantics.
