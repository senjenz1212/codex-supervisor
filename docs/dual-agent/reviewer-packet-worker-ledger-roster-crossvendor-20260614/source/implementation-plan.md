# Implementation Plan

## Intent

Add supervisor-built review packets, typed worker dispatch ledger events, roster preflight, and cross-vendor review gate policy while preserving supervisor ledger truth.

## Files / Modules To Touch

- `supervisor/review_packets.py` - new ReviewPacket and reviewer context receipt builders, packet hashing, packet validation, and missing-context failure reasons.
- `supervisor/worker_dispatch_ledger.py` - new typed worker/reviewer lifecycle event payload helpers and roster preflight event helpers.
- `supervisor/reviewer_registry.py` - expose reviewer provider metadata for roster preflight and cross-vendor selection without changing reviewer execution semantics.
- `supervisor/cursor_agent.py` - include supervisor review packets in Cursor/structured reviewer prompts, require reviewer context receipts in the structured schema, and keep the freeform outcome contract aligned with that requirement.
- `mcp_tools/codex_supervisor_stdio.py` - wire ReviewPacket creation before independent reviewer invocation, persist worker/roster/reviewer events, enforce cross-vendor policy outcomes, and block incomplete reviewer context.
- `supervisor/state.py` - ensure the SQLite ledger event export/read paths preserve the new event payloads without introducing filesystem truth.
- `supervisor/postgres_state.py` - preserve the same event payloads on the Postgres lane where the event export paths are shared.
- `tests/test_reviewer_packets.py` - add packet construction, validation, stable hash, transcript-exclusion, read-only content, and reviewer context receipt tests.
- `tests/test_worker_dispatch_ledger.py` - add typed lifecycle, blocked, cancelled, roster preflight, and boot-failure tests.
- `tests/test_cursor_agent.py` - cover reviewer packet prompt text and structured reviewer schema support for context receipts.
- `tests/test_dual_agent_workflow_driver.py` - add public-boundary gate tests for review_context_incomplete, cross-vendor selection/degradation, Cursor SDK review, and Claude gate verdict preservation.

## Implementation Steps

1. Add RED tests for ReviewPacket construction and validation at the workflow/reviewer invocation boundary. The tests should fail because the packet event, stable `packet_sha256`, complete changed-file coverage, TDD acceptance coverage, and reviewer context receipt validation do not yet exist.
2. Implement `supervisor/review_packets.py` as a pure builder/validator that accepts supervisor-owned inputs: planning artifact refs, git name-status/diff refs, runtime-native receipt ids, changed-file hashes, lesson/policy hashes, and reviewer ids. The builder must not read implementer narrative as truth and must exclude implementer transcript by default.
3. Wire packet creation in `mcp_tools/codex_supervisor_stdio.py` immediately before independent reviewer invocation. Persist a `supervisor_review_packet_created` ledger event and pass either the hashed packet or a read-only validation worktree reference into reviewer instructions.
4. Add reviewer context receipt normalization and validation. A reviewer verdict can satisfy the review gate only if the receipt covers changed files, acceptance criteria, and runtime receipts from the packet; missing coverage emits `review_context_incomplete` and blocks normal acceptance.
5. Add RED tests for typed worker/reviewer lifecycle events, including success, failure, blocked, and cancelled paths. Implement `supervisor/worker_dispatch_ledger.py` helpers and wire existing read-only worker progress/reviewer invocations through those helpers so the ledger, not scratch files, is truth.
6. Add roster preflight tests and implementation. Before fan-out or reviewer routing, probe configured workers/reviewers for availability, record `supervisor_worker_roster_checked`, and distinguish boot failure from task failure by marking the backend unavailable for this run without retry-looping it as a task attempt.
7. Add cross-vendor review policy tests and implementation. When the implementation provider family is known and policy requires diversity, choose a reviewer with a different `provider_family`; if unavailable, block under `reviewer_unavailable_policy=block` or emit `degraded_review_unavailable` under degraded policy.
8. Preserve the existing Cursor SDK rigorous review and Claude gate verdict requirements. The new policy must layer on top of existing reviewer panel and Claude gate checks, not replace either path.
9. Extend transcript/export reads so `read_gate_transcript`, SQLite export, and Postgres export include packet, context receipt, worker lifecycle, roster preflight, evidence attempt, and degraded-review evidence.
10. Keep reviewer prompt/schema changes owned by this slice: Cursor SDK, LiteLLM structured review, and Codex CLI review must all ask for `critical_review.reviewer_context_receipt` with exact packet values.
11. Run focused tests first, then the touched workflow/reviewer/runtime suites. Only after supervisor gates accept should the implementation be split into the requested four commits.

## Risks

- ReviewPacket can accidentally become another self-reported artifact if any field is copied from implementer claims rather than supervisor-owned git/runtime/planning state.
- Cross-vendor policy can produce false confidence if same-vendor review is silently accepted when no alternate reviewer is available.
- Roster preflight can be confused with task execution; boot failures must mark availability for the run without creating retry loops or task-failure noise.
- Reviewer isolation can starve reviewers of necessary context if changed-file content, acceptance criteria, runtime receipts, and explicit dependency refs are not included in the packet.
- Packet hashing can be unstable if timestamps, ordering, or absolute transient paths are included without canonicalization.
- Adding event kinds without transcript/export visibility would make the ledger correct but hard for operators to replay during gate recovery.

## Traceability

- P1 -> `test_review_packet_event_created_for_reviewer_invocation`, `test_review_packet_validation_rejects_missing_changed_file`, `test_review_packet_validation_rejects_missing_acceptance_item`, `test_review_packet_excludes_implementer_transcript_by_default`, `test_reviewer_packet_exposes_read_only_changed_file_contents`
- P2 -> `test_reviewer_context_receipt_missing_changed_file_blocks_gate`, `test_cursor_sdk_rigorous_review_still_runs_on_configured_gates`, `test_claude_gate_verdict_remains_required`
- P3 -> `test_worker_dispatch_events_persist_typed_lifecycle`, `test_worker_blocked_event_persists_required_fields`, `test_worker_cancelled_event_persists_required_fields`, `test_read_only_worker_finding_links_evidence_attempt`
- P4 -> `test_roster_preflight_records_available_and_unavailable_workers`, `test_roster_preflight_happens_before_cross_vendor_selection`, `test_roster_boot_failure_marks_backend_unavailable_without_retry_loop`
- P5 -> `test_roster_preflight_happens_before_cross_vendor_selection`, `test_cross_vendor_review_selects_different_provider_family`, `test_cross_vendor_unavailable_blocks_or_degrades_explicitly`, `test_cursor_sdk_rigorous_review_still_runs_on_configured_gates`, `test_claude_gate_verdict_remains_required`

## Validation Commands

- `python -m pytest tests/test_reviewer_packets.py -q`
- `python -m pytest tests/test_worker_dispatch_ledger.py -q`
- `python -m pytest tests/test_dual_agent_workflow_driver.py -k "review_packet or worker_dispatch or roster_preflight or cross_vendor or cursor_sdk_rigorous or claude_gate" -q`
- `python -m pytest tests/test_reviewer_panel_aggregation.py tests/test_runtime_evidence.py -q`
- `python -m pytest tests/test_codex_supervisor_mcp_stdio.py tests/test_dual_agent_runner.py -q`
