# TDD Plan: Durable Execution Engine Decision

## Cycle 1: Runtime defaults remain hand-rolled

### test_durable_execution_defaults_keep_hand_rolled_runtime

### test_durable_execution_temporal_spike_engine_value_is_explicitly_flagged

Public boundary: `target_config_load`

RED:
- Assert the new durable execution config defaults to `engine=hand_rolled`.
- Assert `temporal_spike_enabled` is false.
- Assert the non-default `engine=temporal_spike` value is covered only when the
  operator also sets the explicit spike flag and task queue.
Maps to: ISS-1, P2
Test names:
- `test_durable_execution_defaults_keep_hand_rolled_runtime`
- `test_durable_execution_temporal_spike_engine_value_is_explicitly_flagged`

GREEN:
- Add a small config model with disabled Temporal spike defaults and an explicit
  non-default spike-value test.

## Cycle 2: Temporal spike is explicit and idempotent

### test_temporal_spike_is_disabled_until_flagged

### test_temporal_spike_uses_idempotency_key_as_workflow_id_with_use_existing

Public boundary: `dual_agent_runner`

RED:
- Assert disabled spike use raises `temporal_spike_disabled`.
- Assert an enabled fake-client spike calls `start_workflow` with workflow id
  equal to the idempotency key and conflict policy `USE_EXISTING`.
- Assert duplicate submit returns the same workflow id and marks retry as
  reattached.
Maps to: ISS-1, P2, P3
Test names:
- `test_temporal_spike_is_disabled_until_flagged`
- `test_temporal_spike_uses_idempotency_key_as_workflow_id_with_use_existing`

GREEN:
- Add `TemporalSubmitLifecycleSpike` and the fake-client-friendly protocol.

## Cycle 3: Compare Temporal spike with Layer-0 reservation

### test_spike_report_compares_temporal_submit_against_layer0_reservation

Public boundary: `dual_agent_runner`

RED:
- Reserve three tasks through actual SQLite `State.reserve_dual_agent_workflow_job`.
- Start the same three tasks through the Temporal fake-client spike.
- Assert each retry returns the same handle, retry is reattached, and the report
  says `default_runtime_changed=false`.
Maps to: ISS-2, P3
Test name: `test_spike_report_compares_temporal_submit_against_layer0_reservation`

GREEN:
- Add a report helper that emits per-task rows and per-system summary booleans.

## Cycle 4: ADR artifact is complete

### test_adr_durable_execution_engine_decision_contains_required_sections

Public boundary: documentation artifact

RED:
- Assert the ADR contains the four options, six scoring criteria, spike result,
  what-replaces, what-stays, migration cost, and explicit no-default-change
  decision.
Maps to: ISS-3, P1, P4
Test name: `test_adr_durable_execution_engine_decision_contains_required_sections`

GREEN:
- Write `docs/adr/0004-durable-execution-engine-decision.md`.

## Regression / Full Suite

- Run `uv run pytest tests/test_durable_execution_engine_spike.py -q`.
- Run the ADR documentation test.
- Run config loading tests if touched.
- Run the full suite.
- Run `git diff --check`.
