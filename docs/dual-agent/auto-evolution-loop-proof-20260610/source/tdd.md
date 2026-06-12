# TDD Plan: Auto-Evolution Loop Proof

## test_auto_evolution_loop_end_to_end_through_axi_and_daemon

Test: `tests/test_auto_evolution_loop.py::test_auto_evolution_loop_end_to_end_through_axi_and_daemon`
Maps to: P1, P3, P4, P5, P6, Slice E1

Red: With any major loop piece missing, the chain cannot reach a rollback draft.
Green: The test seeds recurring failure signals, finalizes a workflow, activates through AXI, runs the daemon task, derives a proposal, approves through AXI, observes instruction change, writes attributed trend rows, schedules weekly audit, and drafts rollback.

## test_auto_evolution_loop_requires_exactly_two_operator_touchpoints

Test: `tests/test_auto_evolution_loop.py::test_auto_evolution_loop_requires_exactly_two_operator_touchpoints`
Maps to: P2, Slice E1

Red: If activation or approval happens without an operator event, the count is not two.
Green: The only operator touchpoints are `autoresearch_experiment_activation_recorded` and `autoresearch_policy_proposal_approved` from the AXI path.

## test_auto_evolution_loop_wire_removal_alarm

Test: `tests/test_auto_evolution_loop.py::test_auto_evolution_loop_wire_removal_alarm`
Maps to: P7, Slice E2

Red: Removing T1, T2, T3, T4, T5, or T7 should not produce a vague final failure.
Green: The parametrized test raises a named `StageBreak` for the disabled wire.

## test_auto_evolution_loop_demo_artifacts_are_internally_consistent

Test: `tests/test_auto_evolution_loop.py::test_auto_evolution_loop_demo_artifacts_are_internally_consistent`
Maps to: P8, Slice E3

Red: Missing demo artifacts, unresolved event ids, or mismatched sha256 hashes fail.
Green: The manifest resolves every event-chain entry and every artifact hash.

## test_loop_doc_is_generated_from_demo_manifest

Test: `tests/test_auto_evolution_loop.py::test_loop_doc_is_generated_from_demo_manifest`
Maps to: P8, Slice E4

Red: A generic loop doc without the manifest path, AXI verbs, cadence knobs, or proof references fails.
Green: `docs/LOOP.md` references the generated manifest and names the actual operator verbs and daemon knobs.

## Regression Suite

Run these focused regressions after the new test:

- `tests/test_autoresearch_generator.py`
- `tests/test_autoresearch_daemon_tasks.py`
- `tests/test_autoresearch_policy_evolution.py`
- `tests/test_codex_supervisor_axi.py`
- `tests/test_request_path_non_execution_guard.py`
- `tests/test_quality_trends.py`
- `tests/test_policy_overlay.py`

## Traceability

- P1 maps to `test_auto_evolution_loop_end_to_end_through_axi_and_daemon`.
- P2 maps to `test_auto_evolution_loop_requires_exactly_two_operator_touchpoints`.
- P3 maps to the daemon/evaluator assertions in the end-to-end test.
- P4 maps to proposal derivation and AXI approval assertions in the end-to-end test.
- P5 maps to instruction-diff and trend-attribution assertions.
- P6 maps to regression and rollback draft assertions.
- P7 maps to `test_auto_evolution_loop_wire_removal_alarm`.
- P8 maps to the demo artifact and loop document tests.
