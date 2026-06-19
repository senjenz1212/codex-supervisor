## test_paired_report_records_full_gate_arm_with_panel_decision

Maps to: Slice 1, P1, P3, P4

Public boundary: `run_paired_acceptance_pilot`

RED: Inject a deterministic reviewer-panel adapter below the paired-pilot boundary, run the pilot, and assert the report has no `supervisor_full_gate` arm, no full-gate decision source, and no panel decision in per-candidate rows.

GREEN: Add the full-gate arm and row fields with injected reviewer-panel results, while keeping the existing public-check arm unchanged.

## test_full_gate_reviewer_packet_excludes_oracle_material

Maps to: Slice 2, P2, P3

Public boundary: `run_paired_acceptance_pilot`

RED: Run the paired pilot with a candidate whose hidden fixture material is present in the bench root, then assert the full-gate row lacks reviewer packet refs and cannot prove hidden material is absent.

GREEN: Build reviewer packets from public evidence only, record packet refs and hashes, and assert serialized packet payloads omit hidden tests, final scores, expected outcomes, oracle labels, hidden commands, and protected path content.

## test_full_gate_unavailable_reviewer_does_not_count_as_accept

Maps to: Slice 1, P3, P5

Public boundary: `run_paired_acceptance_pilot`

RED: Inject a reviewer-panel result with infrastructure unavailable status and assert the report currently has no way to mark `supervisor_full_gate` unavailable without falling back to the public-check decision.

GREEN: Mark the full-gate row and arm metric unavailable, preserve reviewer diagnostics, and keep all calibration authority flags false.

## test_panel_marginal_delta_is_reported_only_when_matched_true_accept_is_computable

Maps to: Slice 3, P4, P5

Public boundary: `run_paired_acceptance_pilot`

RED: Run a fixture set where public-check and full-gate true-accept rates differ, then assert any unconditional panel marginal delta would be misleading.

GREEN: Compute panel marginal delta only with sufficient denominators and matched true-accept status, otherwise return a structured unavailable reason.

## test_full_gate_calibration_report_cannot_create_applyable_policy_claim

Maps to: Slice 3, P5

Public boundary: `run_paired_acceptance_pilot` plus AutoResearch policy derivation guard

RED: Feed a full-gate calibration report to policy derivation and assert an applyable proposal would be created if report-only invariants were ignored.

GREEN: Preserve `metric_applyable=false`, `improvement_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`, and assert derivation returns no proposals.
