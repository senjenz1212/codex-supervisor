# TDD Plan

## test_paired_acceptance_pilot_marks_oracle_coupled_treatment_non_applyable

Contract: OCV-1 Paired Report Oracle-Coupling Metadata

Maps to: P1, P2.

Public boundary: `run_paired_acceptance_pilot` over `tests/fixtures/mergeability_bench`.

RED: The current report has no report label, arm roles, decision-source metadata, oracle-coupling flag, metric applyability field, improvement-claim field, or oracle-coupled gaming flag.

GREEN: The report labels the current oracle-derived acceptance path as `oracle_upper_bound`, records the arm as `oracle_ceiling` with `decision_source=oracle_final_score`, marks it oracle-coupled, sets `metric_applyable=false`, sets `improvement_claim_allowed=false`, and includes `oracle_coupled_treatment_arm` in gaming flags.

## test_paired_acceptance_pilot_preserves_baseline_false_accept_visibility

Contract: OCV-2 Preserve Calibration Signal Without Claiming Improvement

Maps to: P2, P4.

Public boundary: `run_paired_acceptance_pilot`.

RED: The existing tests assert `supervisor_false_accept_rate == 0.0` and a negative Supervisor delta as though the arm measured independent Supervisor behavior.

GREEN: Baseline false accepts remain visible, the oracle ceiling still has zero false accepts, and the test asserts that this metric is non-applyable and not an improvement claim.

## test_paired_acceptance_pilot_exports_validity_metadata_with_replay_artifacts

Contract: OCV-3 Same Candidate Pool And Artifact Export Remain Replayable

Maps to: P1, P4.

Public boundary: `run_paired_acceptance_pilot(..., output_dir=...)`.

RED: Exported `paired_acceptance_report.json` and `per_task_results.jsonl` lack validity metadata and cannot reconstruct why the report was non-applyable.

GREEN: Exported artifacts include report-level validity fields and per-row decision-source metadata while preserving candidate-pool hash, receipts, and runtime-native evidence.

## test_paired_acceptance_report_oracle_coupling_blocks_policy_derivation

Contract: OCV-4 Policy Derivation Rejects Oracle-Coupled Reports

Maps to: P3.

Public boundary: `derive_policy_evolution_proposals_from_report` and `report_contains_derivable_policy_record`.

RED: Policy derivation does not explicitly inspect oracle-coupling or improvement-claim metadata, so a future report shape could bypass the measurement-validity guard.

GREEN: A report record with `oracle_coupled_treatment_arm`, `metric_applyable=false`, or `improvement_claim_allowed=false` produces no policy proposal and is reported as non-derivable.

## test_existing_autoresearch_report_only_invariants_remain_green

Contract: OCV-5 Existing Report-Only Invariants Remain Green

Maps to: P4.

Public boundary: existing AutoResearch and mergeability report-only tests.

RED: The validity metadata weakens or bypasses `default_change_allowed=false`, `policy_mutated=false`, or `gate_advanced=false`.

GREEN: Existing report-only invariants remain false, and no gate authority changes in this slice.
