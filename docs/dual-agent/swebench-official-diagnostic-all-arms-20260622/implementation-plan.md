## Files / Modules To Touch

- `supervisor/swe_bench_mergeability.py` adds the official all-arms diagnostic runner and report builder.
- `supervisor/swe_bench_mergeability_cli.py` adds the CLI flag routing official replay arguments into the diagnostic runner.
- `tests/test_swe_bench_pro_mergeability_bridge.py` adds public-boundary diagnostic tests over official-shaped records.
- `docs/dual-agent/swebench-official-diagnostic-all-arms-20260622/*.md` records the PRD-to-TDD artifacts and gate findings.

## Risks

- A one-good one-bad sample would leave matched true-accept unavailable, so the success test uses two oracle-good rows and one oracle-bad row.
- Surfacing only nested replay evidence would let an operator miss hidden-isolation status, so the report builder exposes the leak check directly.
- A configured panel quality reject is different from panel infrastructure unavailable; the report must preserve that distinction while still requiring full roster availability.

## Traceability

P1 is covered by test_official_all_arms_diagnostic_completes_with_matched_tar_and_no_claim and test_official_all_arms_diagnostic_refuses_claim_when_full_gate_unavailable.

P2 is covered by test_official_all_arms_diagnostic_is_unavailable_when_oracle_is_unavailable, test_official_all_arms_diagnostic_refuses_claim_when_full_gate_unavailable, test_official_all_arms_diagnostic_refuses_claim_when_baseline_unavailable, and test_official_all_arms_diagnostic_blocks_hidden_field_leak.

P3 is covered by test_official_all_arms_diagnostic_completes_with_matched_tar_and_no_claim and test_official_all_arms_diagnostic_cli_writes_unavailable_report_without_panel.

P4 is covered by all six diagnostic tests because every outcome asserts non-applyable or no-claim flags.
