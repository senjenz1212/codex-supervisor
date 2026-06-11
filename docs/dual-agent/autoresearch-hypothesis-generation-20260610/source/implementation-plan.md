# Implementation Plan

## Files / Modules To Touch

- `supervisor/autoresearch/generator.py`: add the generator config, signal
  clustering, draft creation, activation helper, and runnable auto-runner.
- `supervisor/autoresearch/__init__.py`: export the generator and runner APIs
  for tests and future daemon wiring.
- `supervisor/config.py`: add the conservative AutoResearch generator and runner
  guard configuration.
- `config.example.yaml`: document the conservative default limits for local and
  production operators.
- `supervisor/state.py`: add SQLite table DDL and queue state methods.
- `supervisor/postgres_state.py`: add matching Postgres table DDL and queue
  state methods.
- `supervisor/schema_migrations.py`: add SQLite migration version 9.
- `migrations/versions/20260610_0003_autoresearch_experiment_queue.py`: add the
  Alembic migration for the Postgres lane.
- `tests/test_autoresearch_generator.py`: add public-boundary tests for signal
  drafting, activation, immutable report-only rows, and weekly caps.
- `tests/test_schema_migrations.py`: assert the SQLite migration shape.
- `tests/test_postgres_ledger_lane.py`: assert the Postgres inline schema and
  Alembic migration stay structurally aligned.

## Risks

- The workflow source artifact system prefers files under `source/`, so weak seed
  artifacts can block the PRD gate even when top-level documents look complete.
- A naive runner could bypass the durable evaluator lane and silently reintroduce
  direct subprocess execution.
- Mutable path inference can be too broad if default paths are not conservative,
  creating noisy or unsafe experiment candidates.
- Idempotency must be enforced by `signal_key`; otherwise every daemon scan could
  create another experiment for the same evidence cluster.
- Weekly caps must be based on persisted start timestamps, not local process
  counters, so restarts preserve budget behavior.

## Traceability

- P1 is implemented by `generate_autoresearch_experiment_drafts`,
  `State.upsert_autoresearch_experiment_draft`, and
  `test_autoresearch_signal_generator_drafts_one_experiment_for_repeated_taxonomy_failures`.
- P2 is implemented by `activate_autoresearch_experiment`,
  `run_runnable_autoresearch_experiments`, and
  `test_autoresearch_draft_cannot_run_until_operator_marks_runnable`.
- P2's failure path is implemented by the runner report acceptance check and
  `test_autoresearch_auto_runner_fails_rejected_evaluator_report`.
- P3 is implemented by immutable path classification in the generator and
  `test_autoresearch_immutable_surface_signal_becomes_report_only`.
- P4 is implemented by `count_autoresearch_experiments_started_since`, the runner
  cap logic, and `test_autoresearch_auto_runner_respects_weekly_cap`.
- `test_autoresearch_generator_config_loads_budget_guards_from_supervisor_config`
  covers the budget and timeout guard configuration used by P4.
- `test_autoresearch_signal_generator_reads_reviewer_probe_and_lesson_signals`
  covers the non-taxonomy signal families named by P1.
