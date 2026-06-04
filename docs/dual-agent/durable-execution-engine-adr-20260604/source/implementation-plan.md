# Implementation Plan

## Files / Modules To Touch

- `supervisor/durable_execution_engine_spike.py`
- `supervisor/config.py`
- `config.example.yaml`
- `tests/test_durable_execution_engine_spike.py`
- `tests/test_durable_execution_engine_adr.py`
- `docs/adr/0004-durable-execution-engine-decision.md`
- `docs/dual-agent/durable-execution-engine-adr-20260604/source/`

## Steps

1. Add a disabled durable execution config model with `engine=hand_rolled`,
   `temporal_spike_enabled=false`, and a Temporal task queue name.
2. Add a report-only Temporal submit lifecycle spike module that accepts a
   fake-client-compatible protocol and uses `USE_EXISTING` conflict semantics.
3. Add tests for disabled defaults, explicit enablement, workflow-id mapping,
   duplicate reattach, and comparison against the real Layer-0 reservation
   method.
4. Add the ADR with scored options, recommendation, spike result, replace-vs-
   stay boundary, and migration cost.
5. Run focused tests, ADR tests, relevant config tests, full suite, and diff
   check.
6. Submit the completed slice through the durable supervised workflow and commit
   only after outcome review accepts.

## Risks

- A fake Temporal client proves argument shape and duplicate-handle semantics,
  but it does not prove live Temporal service behavior or deployment costs.
- Adding a config section could look like adoption. The defaults and ADR must be
  explicit that this is disabled and report-only.
- Restate and DBOS may be better references for narrower subproblems than for a
  full runtime replacement. The ADR should preserve that nuance.
- Temporal adoption could remove lifecycle code, but it would add service
  operation and activity-hosting costs.

## Traceability

- P1 / ISS-3: `test_adr_durable_execution_engine_decision_contains_required_sections`.
- P2 / ISS-1: `test_durable_execution_defaults_keep_hand_rolled_runtime`,
  `test_durable_execution_temporal_spike_engine_value_is_explicitly_flagged`,
  `test_temporal_spike_is_disabled_until_flagged`.
- P3 / ISS-1, ISS-2:
  `test_temporal_spike_uses_idempotency_key_as_workflow_id_with_use_existing`,
  `test_spike_report_compares_temporal_submit_against_layer0_reservation`.
- P4 / ISS-3: `test_adr_durable_execution_engine_decision_contains_required_sections`.
