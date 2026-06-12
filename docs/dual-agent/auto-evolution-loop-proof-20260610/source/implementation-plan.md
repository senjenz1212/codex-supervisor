# TDD Grill Findings / Implementation Plan

### Finding T1: The first test must be a full chain proof.

Status: resolved

- Risk: A collection of unit tests could miss integration liveness.
- Resolution: The primary test drives the complete recurring-failure to rollback-proposal chain before helper assertions.

### Finding T2: Wire-removal cases must name the broken arrow.

Status: resolved

- Risk: Future regressions would fail only with end state not reached.
- Resolution: Parametrized negative cases assert the expected stage name for T1, T2, T3, T4, T5, and T7.

### Finding T3: AXI path must be real for human touchpoints.

Status: resolved

- Risk: Helper calls could bypass operator CLI semantics.
- Resolution: Activation and proposal approval are driven through `codex_supervisor_axi.main`.

## Files / Modules To Touch

- `supervisor/autoresearch/generator.py` for the generated-attempt baseline metric used by proposal-grade live reports.
- `tests/test_auto_evolution_loop.py` for the deterministic proof, wire-removal alarms, and demo artifact consistency checks.
- `docs/LOOP.md` for the operator-facing loop description generated from the demo manifest.
- `docs/dual-agent/auto-evolution-loop-proof-20260610/demo-manifest.json` for the event-id and hash proof.
- `docs/dual-agent/auto-evolution-loop-proof-20260610/demo-report.json` for the accepted AutoResearch report export.
- `docs/dual-agent/auto-evolution-loop-proof-20260610/demo-proposal.json` for the derived overlay proposal export.
- `docs/dual-agent/auto-evolution-loop-proof-20260610/demo-trends.json` for overlay-attributed trend rows.

## Risks

- A generated experiment may run successfully but still lack proposal-grade evidence if the attempt does not include a policy-overlay candidate and positive metric delta.
- A proof that calls helper functions directly could bypass the AXI operator path and hide a broken CLI handoff.
- Regression detection can look green with insufficient post-overlay trend rows unless the fixture seeds both before and after windows.
- Demo artifacts can drift from the ledger if event ids and sha256 hashes are not checked by tests.

## Traceability

- P1, P3, P4, P5, and P6 map to `test_auto_evolution_loop_end_to_end_through_axi_and_daemon`.
- P2 maps to `test_auto_evolution_loop_requires_exactly_two_operator_touchpoints`.
- P7 maps to `test_auto_evolution_loop_wire_removal_alarm`.
- P8 maps to `test_auto_evolution_loop_demo_artifacts_are_internally_consistent` and `test_loop_doc_is_generated_from_demo_manifest`.
- `test_auto_evolution_loop_end_to_end_through_axi_and_daemon` covers Slice E1 and TDD-1 through TDD-8.
- `test_auto_evolution_loop_wire_removal_alarm` covers Slice E2 and the T1/T2/T3/T4/T5/T7 removal alarms.
- `test_auto_evolution_loop_demo_artifacts_are_internally_consistent` covers Slice E3 and the manifest/hash/event-id proof.
