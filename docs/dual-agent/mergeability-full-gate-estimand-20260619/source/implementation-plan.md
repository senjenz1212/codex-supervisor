## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `supervisor/autoresearch/policy_evolution.py`

## Approach

First add a public-boundary failing test that expects `run_paired_acceptance_pilot` to emit a `supervisor_full_gate` arm without removing `supervisor_candidate_review`. Then add an injectable reviewer-panel path below the paired-pilot interface, using deterministic reviewer results in tests and existing reviewer-panel payload vocabulary in report rows. Next add oracle-isolated reviewer packet construction and leak checks, reusing the existing forbidden-key and forbidden-text strategy where possible. Finally add metric derivation for panel marginal delta while preserving report-only authority flags.

## Risks

- Reviewer-panel wiring can accidentally call live model providers from unit tests if adapters are not injected below the report boundary.
- Reviewer packets can leak hidden oracle information through serialized task, candidate, command, or protected-path payloads unless the same leak detector guards every packet field.
- Full-gate unavailable states can be mistaken for acceptance if the report falls back to the public-check arm instead of marking the full-gate metric unavailable.

## Traceability

P1 maps to `test_paired_report_records_full_gate_arm_with_panel_decision` and the new full-gate arm in `run_paired_acceptance_pilot`.

P2 maps to `test_full_gate_reviewer_packet_excludes_oracle_material` and the reviewer packet leak detector.

P3 maps to `test_full_gate_unavailable_reviewer_does_not_count_as_accept` and injected reviewer-panel unavailable payloads.

P4 maps to `test_panel_marginal_delta_is_reported_only_when_matched_true_accept_is_computable` and report-level arm disagreement fields.

P5 maps to `test_full_gate_calibration_report_cannot_create_applyable_policy_claim` and existing AutoResearch policy derivation guards.
