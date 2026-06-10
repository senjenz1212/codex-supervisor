# TDD Plan: Supervisor Lessons Loop

## test_failed_run_writes_supervisor_lesson_record

Maps to: Slice 1, Slice 2, P1, P5.

RED: create a blocked `dual_agent_gate_result` with a P11 failure and assert no lesson is persisted before the hook exists.

GREEN: run the deterministic lesson recorder, assert one lesson row, one `supervisor_lesson_recorded` event, populated taxonomy/root-cause/remediation fields, and idempotency on a second recorder call.

## test_matching_future_gate_injects_lesson_and_records_hash

Maps to: Slice 3, Slice 4, P2, P3.

RED: persist a prior lesson and assert the future gate instruction lacks known-failure guidance.

GREEN: build future gate start kwargs and assert the known-failure block, lesson ids, `supervisor_lesson_injection` event, advisory flag, and SHA-256 hash are present.

## test_non_matching_task_class_or_gate_does_not_inject

Maps to: Slice 3, P2, P4.

RED: add a lesson for a different task class or gate and show it leaks into the current instruction.

GREEN: assert the current instruction has no known-failure block, no lesson ids, and no injection event.

## test_workflow_lesson_snapshot_pins_gate_block_hashes

Maps to: Slice 4, P3.

RED: build a workflow route without recording per-gate lesson hashes.

GREEN: assert the route lesson snapshot stores matching gate block text, lesson ids, advisory metadata, and the same SHA-256 as the canonical block.

## test_injection_hash_is_stable_and_handoff_reconstructs_block

Maps to: Slice 4, P3.

RED: build the same lesson block twice and observe unstable ordering or missing handoff metadata.

GREEN: assert stable block text, stable hash, handoff packet fields, and replay recomputation from the stored block.

## test_reviewer_disagreement_and_sequence_failures_produce_lessons

Maps to: Slice 2, P5.

RED: reviewer disagreement and duplicate gate signatures produce no reusable lesson.

GREEN: assert reviewer disagreement maps to FM-2.4 and repeated gate signatures map to FM-1.3.

## Regression Commands

Maps to: Slice 5, P4.

RED: existing workflow driver, lead invocation, schema migration, Postgres lane, and artifact-export tests fail after metadata changes.

GREEN: run the focused and related suites with `.venv/bin/python -m pytest` and require all selected tests to pass.
