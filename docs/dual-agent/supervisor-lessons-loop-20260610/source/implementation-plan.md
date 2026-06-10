# Implementation Plan

## Files / Modules To Touch

- `supervisor/lessons.py` for canonical lesson derivation, block building, stable hashes, and run-end recording helpers.
- `supervisor/state.py` for SQLite lesson schema and public record/query/list APIs.
- `supervisor/schema_migrations.py` for the forward migration that adds `supervisor_lessons`.
- `supervisor/postgres_state.py` and `migrations/versions/20260604_0001_postgres_event_job_lane.py` for Postgres lane structural parity.
- `supervisor/dual_agent_lead.py` and `supervisor/dual_agent_runner.py` for handoff packet and transcript metadata.
- `mcp_tools/codex_supervisor_stdio.py` for workflow route lesson snapshots, gate-start injection, and run-end lesson recording.
- `tests/test_supervisor_lessons.py` and `tests/test_schema_migrations.py` for public-boundary and migration coverage.

## Risks

- Lesson injection could drift from the route snapshot if gate-start queries are not pinned; use the workflow lesson snapshot when available.
- Lesson records could duplicate on durable retry; use deterministic lesson ids and `INSERT OR IGNORE`.
- Postgres schema could drift from Alembic migration; keep the structural parity test green.
- The block could be treated as gate evidence; mark events and handoff metadata as advisory and leave acceptance predicates unchanged.

## Traceability

- P1 is covered by `test_failed_run_writes_supervisor_lesson_record`.
- P2 is covered by `test_matching_future_gate_injects_lesson_and_records_hash` and `test_non_matching_task_class_or_gate_does_not_inject`.
- P3 is covered by `test_workflow_lesson_snapshot_pins_gate_block_hashes` and `test_injection_hash_is_stable_and_handoff_reconstructs_block`.
- P4 is covered by workflow driver regression tests and advisory metadata assertions.
- P5 is covered by `test_reviewer_disagreement_and_sequence_failures_produce_lessons`.
