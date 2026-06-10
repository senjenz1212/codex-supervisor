# Issues: Supervisor Lessons Loop

## Slice 1 - Persist Supervisor Lesson Records

Priority: P0

Scope: add a `supervisor_lessons` table, SQLite migration, Postgres schema mirror, and state APIs for record/query/list operations.

Acceptance Criteria:

- [ ] `State.record_supervisor_lesson` writes a stable lesson row.
- [ ] Repeating the same lesson input returns the existing row without duplication.
- [ ] SQLite migrations and Postgres structural tests include the new table and index.

## Slice 2 - Derive Lessons From Run-End Evidence

Priority: P0

Scope: derive lessons from failure taxonomy, reviewer disagreement, repeated gate failures, and drift/probe cohort evidence without model calls.

Acceptance Criteria:

- [ ] A blocked gate with `P11` failure taxonomy writes a task-verification lesson.
- [ ] Reviewer panel non-acceptance writes a reviewer-disagreement lesson.
- [ ] Duplicate gate handoff signatures write a repeated-gate lesson.

## Slice 3 - Inject Matching Lessons Into Lead Instructions

Priority: P0

Scope: query top-N lessons by `task_class` and `gate`, build the canonical "Known failure modes to verify before claiming" block, and append it to the lead instruction.

Acceptance Criteria:

- [ ] Matching task class and gate inject the lesson block.
- [ ] Non-matching task class or gate does not inject.
- [ ] The lead prompt and handoff packet include the block metadata.

## Slice 4 - Record Injection Hashes For Replay

Priority: P1

Scope: write `supervisor_lesson_injection` events and workflow route lesson snapshots with block text, lesson ids, and SHA-256 hashes.

Acceptance Criteria:

- [ ] The stored hash equals `sha256(block)`.
- [ ] Replay can reconstruct the block from ledger or handoff fields.
- [ ] The route snapshot pins per-gate hashes at workflow start.

## Slice 5 - Preserve Gate Authority

Priority: P0

Scope: keep lesson records advisory so existing P1/P2/P3/P11/P12/P13/P14, runtime-native evidence, Cursor review, and typed outcomes remain authoritative.

Acceptance Criteria:

- [ ] Lesson injection never creates a passing receipt.
- [ ] Reviewer and outcome gates still block when their existing probes fail.
- [ ] Config defaults and policy mutation fields remain unchanged.
