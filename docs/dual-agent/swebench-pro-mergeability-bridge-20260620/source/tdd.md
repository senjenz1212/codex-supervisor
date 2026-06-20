# TDD Plan

## test_public_packet_excludes_hidden_oracle_material

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 1; PRD promises P1, P2, P3.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_public_packet_excludes_hidden_oracle_material`. Build a fixture SWE-bench Pro instance containing public metadata plus forbidden hidden fields, then call the bridge public packet/report path. Assert the serialized public packet contains instance id, repo, base commit, problem statement, public checkout hash/ref, candidate artifact hash, and `s_probe_substrate`, but does not contain FAIL_TO_PASS, PASS_TO_PASS, test_patch, final_score, oracle_accept, expected_outcome, or hidden command strings.

GREEN: Add the smallest bridge module and public packet builder needed to satisfy the test.

## test_oracle_material_in_public_packet_triggers_isolation_failure

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 1; PRD promises P1, P3, P6.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_oracle_material_in_public_packet_triggers_isolation_failure`. Inject a forbidden key or hidden test path into public packet input and assert the row/report records `oracle_isolation_violation`, marks the affected arm unavailable or red, and never accepts the row.

GREEN: Reuse or generalize the existing oracle reference detector so SWE-bench Pro bridge packets fail closed.

## test_s_probe_substrate_is_explicit_and_required

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 1 and Slice 2; PRD promises P2, P5.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_s_probe_substrate_is_explicit_and_required`. Pass a bridge input with no public-probe substrate and assert it is rejected. Then pass the static substrate and assert the report records patch applicability plus curated public lint/build commands as the selected substrate.

GREEN: Add substrate validation and report fields for `public_static_patch_probe`.

## test_arm_decisions_are_recorded_before_oracle_results

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 2; PRD promises P3, P5.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_arm_decisions_are_recorded_before_oracle_results`. Build fixture baseline, S_probe, and S_full decisions, attach oracle outcomes, and assert the report records decision-phase hashes or frozen decision rows before oracle labels are summarized.

GREEN: Add frozen decision row construction and post-decision oracle attachment.

## test_full_gate_reviewer_unavailable_is_not_imputed_as_accept

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 2; PRD promises P4, P6.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_full_gate_reviewer_unavailable_is_not_imputed_as_accept`. Provide an available S_probe accept and an unavailable reviewer-panel result. Assert S_full is unavailable, accepted_count does not include that row, and report-only invariants remain false.

GREEN: Add S_full composition that requires both S_probe accept and available reviewer-panel accept.

## test_full_gate_can_disagree_with_s_probe_and_records_delta

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 2; PRD promises P4, P5.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_full_gate_can_disagree_with_s_probe_and_records_delta`. Provide S_probe accept and reviewer-panel deny. Assert S_probe and S_full decisions differ, disagreement appears in per-row output, and panel marginal delta is reportable when matched-TAR conditions allow it.

GREEN: Add disagreement preservation and panel marginal summary fields.

## test_pass_to_pass_regression_contributes_to_no_regression_status

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 3; PRD promises P3, P5.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_pass_to_pass_regression_contributes_to_no_regression_status`. Provide a row whose FAIL_TO_PASS passes but PASS_TO_PASS fails. Assert oracle_accept is false or degraded according to the bridge contract and no-regression reporting records the regression.

GREEN: Add PASS_TO_PASS status parsing and regression summary.

## test_oracle_ceiling_is_coupled_and_never_supervisor_improvement

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 3; PRD promises P5, P6.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_oracle_ceiling_is_coupled_and_never_supervisor_improvement`. Assert oracle ceiling has `oracle_coupled=true`, `improvement_claim_allowed=false`, and cannot create an applyable policy proposal.

GREEN: Add oracle ceiling summary and policy derivation guard compatibility.

## test_far_tar_frr_denominators_use_post_decision_oracle_labels

Public boundary: `swebench_pro_mergeability_bridge_report`.

Maps to: Slice 3; PRD promises P3, P5.

RED: Add `tests/test_swe_bench_pro_mergeability_bridge.py::test_far_tar_frr_denominators_use_post_decision_oracle_labels`. Assert FAR, TAR, FRR, n_bad, n_good, confidence intervals, and matched-TAR status are computed from post-decision oracle outcomes while decision rows remain unchanged.

GREEN: Reuse mergeability summary helpers or equivalent public behavior.

## test_existing_swe_bench_pass_at_k_behavior_remains_green

Public boundary: existing SWE-bench Pro solve-rate report and CLI tests.

Maps to: Slice 4; PRD promise P7.

RED: Keep `tests/test_swe_bench_pro_eval.py` in the declared test set and assert existing pass-at-k report fields remain unchanged.

GREEN: Avoid modifying the existing solve-rate adapter except for additive imports or documentation if needed.

## test_existing_mergeability_behavior_remains_green

Public boundary: existing mergeability paired report tests.

Maps to: Slice 2 and Slice 3; PRD promises P4, P5, P6.

RED: Keep the existing mergeability full-gate, oracle-isolation, and report-only tests in the declared test set.

GREEN: Reuse existing helpers without weakening current fixture-scale behavior.

## Translation Audit

Every PRD promise has at least one public-boundary test. The first implementation test hits the bridge report boundary, not a private helper. The plan follows one RED to one GREEN increments and keeps live SWE-bench Pro execution, policy mutation, and FrontierCode-style maintainer rubric out of scope.
