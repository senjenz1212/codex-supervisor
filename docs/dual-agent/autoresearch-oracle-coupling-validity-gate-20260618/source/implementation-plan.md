# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `supervisor/autoresearch/policy_evolution.py`
- `tests/test_mergeability_bench.py`
- `tests/test_autoresearch_policy_evolution.py`

## Implementation Steps

1. Add a small validity metadata construction path inside
   `supervisor/mergeability_bench.py` near the paired acceptance pilot report
   builder. Keep the existing oracle-derived calculation intact for this slice,
   but relabel it as an oracle ceiling and mark its decision source as
   `oracle_final_score`.
2. Extend the paired report summary and exported per-task rows with
   `report_label`, per-arm `arm_role`, per-arm `decision_source`,
   per-arm `oracle_coupled`, `metric_applyable`,
   `improvement_claim_allowed`, and `gaming_flags`.
3. Rewrite the existing mergeability pilot tests in
   `tests/test_mergeability_bench.py` so they assert validity metadata and
   non-applyability rather than treating zero oracle-ceiling false accepts as
   independent Supervisor improvement.
4. Extend `supervisor/autoresearch/policy_evolution.py` so derivation rejects
   report records whose gaming flags include `oracle_coupled_treatment_arm` or
   whose validity metadata says `metric_applyable=false` or
   `improvement_claim_allowed=false`.
5. Add policy-evolution regression coverage in
   `tests/test_autoresearch_policy_evolution.py`, then run the focused
   mergeability and policy-evolution tests.

## Risks

- The existing paired pilot already exposes `applyable_policy_proposal=false`;
  the new fields must tighten the evidence claim without making the report
  shape confusing or duplicative.
- Renaming the oracle-derived arm can break callers that still expect the
  previous key, so compatibility aliases may be needed while the arm role makes
  the true provenance unambiguous.
- A statistical equality warning can overfire on tiny corpora, so declared
  decision-source provenance must remain the blocking signal.
- Policy derivation must reject invalid evidence without blocking unrelated
  accepted reports that already satisfy evaluator-quality and report-only
  invariants.

## Traceability

P1 is implemented by `test_paired_acceptance_pilot_marks_oracle_coupled_treatment_non_applyable` and `test_paired_acceptance_pilot_exports_validity_metadata_with_replay_artifacts`.

P2 is implemented by `test_paired_acceptance_pilot_marks_oracle_coupled_treatment_non_applyable` and `test_paired_acceptance_pilot_preserves_baseline_false_accept_visibility`.

P3 is implemented by `test_paired_acceptance_report_oracle_coupling_blocks_policy_derivation`.

P4 is implemented by `test_paired_acceptance_pilot_preserves_baseline_false_accept_visibility`, `test_paired_acceptance_pilot_exports_validity_metadata_with_replay_artifacts`, and `test_existing_autoresearch_report_only_invariants_remain_green`.
