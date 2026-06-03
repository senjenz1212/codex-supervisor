# TDD Plan: Agentic Eval Bridge

## test_agentic_eval_assembler_emits_runner_loadable_dataset

Maps to: Slice 1, P1, P4.

RED: a labeled-set case cannot be transformed into a three-arm dataset task
without hand-authored arm data.

GREEN: implement `assemble_agentic_eval_task` so a fake workflow runner is
called once per required mode and the resulting dataset is accepted by
`agentic_eval_runner`.

## test_agentic_eval_assembler_enforces_equal_total_budget_and_split

Maps to: Slice 2, P2, P5.

RED: fan-out arms can expose extra worker budget or drift from the lead-direct
total budget.

GREEN: implement `compute_arm_budget_split` and dataset validation so arm-level
total budgets are equal and agentic lead/worker shares do not exceed the total.

## test_agentic_eval_bridge_record_replay_is_deterministic

Maps to: Slice 3, P3, P4.

RED: recorded arm cassettes cannot be replayed to the same report digest.

GREEN: write assembled datasets with cassette refs and prove two fixture-replay
runner invocations produce identical `report_sha256` values.

## test_agentic_eval_live_cli_refuses_without_allow_live_calls

Maps to: Slice 3, P3, P5.

RED: invoking the live CLI can run workflows by default.

GREEN: require `--allow-live-calls` before any live workflow runner can be
constructed.

## test_agentic_eval_bridge_replay_does_not_call_live_runner

Maps to: Slice 3, P4, P5.

RED: replay can accidentally invoke a supplied live workflow runner.

GREEN: keep default replay on `fixture_replay` and assert the forbidden runner
is never called.

## test_agentic_eval_bridge_report_only_policy_snapshot

Maps to: Slice 3, P5.

RED: the bridge report can imply default fan-out enablement or policy mutation.

GREEN: assert `default_change_allowed` is false, `agentic_lead_policy_snapshot`
is off, and no config/state paths are touched by the bridge.
