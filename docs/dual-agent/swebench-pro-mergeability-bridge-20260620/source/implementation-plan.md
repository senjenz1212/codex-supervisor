# Implementation Plan

## Public Boundary

Create a new SWE-bench Pro mergeability bridge interface that builds a report from fixture-shaped instance records, candidate arm artifacts, explicit public-probe substrate configuration, reviewer-panel results, and post-decision oracle outcomes. Keep the interface separate from the existing SWE-bench pass-at-k report builder.

## Files / Modules To Touch

- `supervisor/swe_bench_mergeability.py`: new bridge module with public packet construction, oracle-isolation checks, frozen decision rows, and report summaries.
- `supervisor/mergeability_bench.py`: reuse or expose existing summary helpers only if the bridge needs shared FAR/TAR/FRR behavior.
- `tests/test_swe_bench_pro_mergeability_bridge.py`: public-boundary tests for the new bridge report path.
- `tests/test_swe_bench_pro_eval.py`: existing solve-rate non-regression coverage remains in the declared test run.
- `docs/dual-agent/swebench-pro-mergeability-bridge-20260620/source/tdd.md`: declared public-boundary test contract.

## Steps

1. Add public packet construction with oracle-field redaction and leak detection.
2. Add S_probe substrate validation for `public_static_patch_probe`.
3. Add frozen arm decision rows for baseline, S_probe, and S_full.
4. Add post-decision oracle attachment for FAIL_TO_PASS and PASS_TO_PASS outcomes.
5. Add FAR/TAR/FRR summaries, matched-TAR status, no-regression reporting, oracle ceiling labels, and report-only invariants.
6. Add tests for the bridge plus non-regression tests for existing SWE-bench and mergeability behavior.

## Risks

- A static public probe can be weaker than future repo-specific public tests, so the report must label `public_static_patch_probe` clearly and never imply parity with hidden FAIL_TO_PASS/PASS_TO_PASS execution.
- SWE-bench Pro hidden oracle fields are easy to leak by copying raw instance dictionaries into reviewer packets, so the bridge should build allowlisted public packets rather than filtering after serialization.
- Reusing existing mergeability helpers may accidentally import local-fixture assumptions, so shared helpers should be called only where their estimator semantics match this bridge.
- A reviewer-panel outage must not collapse into an accept or a deny; S_full must be unavailable so the report preserves missing evidence.

## Traceability

- P1 and P3 are covered by `test_public_packet_excludes_hidden_oracle_material`, `test_oracle_material_in_public_packet_triggers_isolation_failure`, and `test_arm_decisions_are_recorded_before_oracle_results`.
- P2 is covered by `test_s_probe_substrate_is_explicit_and_required`.
- P4 is covered by `test_full_gate_reviewer_unavailable_is_not_imputed_as_accept` and `test_full_gate_can_disagree_with_s_probe_and_records_delta`.
- P5 is covered by `test_far_tar_frr_denominators_use_post_decision_oracle_labels`, `test_pass_to_pass_regression_contributes_to_no_regression_status`, and `test_oracle_ceiling_is_coupled_and_never_supervisor_improvement`.
- P6 is covered by `test_oracle_material_in_public_packet_triggers_isolation_failure`, `test_full_gate_reviewer_unavailable_is_not_imputed_as_accept`, and `test_oracle_ceiling_is_coupled_and_never_supervisor_improvement`.
- P7 is covered by `test_existing_swe_bench_pass_at_k_behavior_remains_green`.

## Verification

Run the new bridge tests, existing `tests/test_swe_bench_pro_eval.py`, and the focused mergeability tests named in the TDD plan. Runtime evidence must include declared and executed test names, public-packet oracle-isolation proof, and report-only guardrail proof.
