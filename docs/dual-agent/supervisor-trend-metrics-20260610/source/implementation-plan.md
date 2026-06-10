# Implementation Plan

## Summary

Implement a small quality-trend subsystem that derives metrics from the existing supervisor ledger, persists those metrics in SQLite/Postgres state tables, and exposes a read-only CLI query. The work stays observational: it records trend rows and P11 audit counters, but it does not alter gate decisions, reviewer authority, or policy defaults.

## Files / Modules To Touch

- `supervisor/state.py`
- `supervisor/postgres_state.py`
- `supervisor/schema_migrations.py`
- `supervisor/quality_trends.py`
- `scripts/run_supervisor_trend_metrics.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `migrations/versions/20260610_0002_supervisor_quality_trends.py`
- `tests/test_quality_trends.py`
- `tests/test_schema_migrations.py`
- `tests/test_postgres_ledger_lane.py`

## Steps

1. Add the `supervisor_quality_trends` table and state helpers for upsert, audit update, aggregate query, and row counting.
2. Add `supervisor/quality_trends.py` to decode existing gate/route/runtime evidence events, compute accepted status, first-pass acceptance, revision rounds, and `time_to_accepted_outcome_s`, and run sampled P11 false-accept audits through runtime evidence.
3. Add the CLI query/record/audit entrypoint in `scripts/run_supervisor_trend_metrics.py`, with query constrained to read-only state access.
4. Wire run-end trend recording into `_workflow_result` as best-effort telemetry with `observational_only=true` and `gate_authority=unchanged`.
5. Keep Postgres inline schema and Alembic migrations structurally equivalent to the SQLite lane.
6. Add focused tests for metric math, false-accept audit, read-only query, CLI JSON output, migration shape, and supervisor-final-status precedence.

## Risks

- Trend math can accidentally count Claude acceptance instead of supervisor final acceptance. Mitigation: prefer `supervisor_final_status`, then `status`, and only fall back to Claude status when no supervisor value exists.
- P11 audit can become expensive if it executes too many test commands. Mitigation: keep sampling bounded and expose `sample_size` and `test_timeout_s`.
- Query code can quietly become a write path through lazy recomputation. Mitigation: persist rows separately and assert event/row counts do not change after query.
- Migration drift can break Postgres deployments. Mitigation: add the Alembic migration and extend the existing inline-vs-migration structural equivalence test.

## Traceability

- P1 -> `test_quality_trends_record_run_computes_first_pass_revision_rounds_and_time_to_accept`
- P1 -> `test_forward_migration_adds_supervisor_quality_trends`
- P2 -> `test_quality_trends_sampled_p11_audit_catches_false_accept`
- P3 -> `test_quality_trends_query_filters_by_task_class_and_gate_without_writes`
- P3 -> `test_quality_trends_cli_query_is_read_only_json`
- P4 -> `test_quality_trends_metrics_do_not_advance_or_block_gates`
- P4 -> `test_quality_trends_prefers_supervisor_final_status_over_claude_status`
