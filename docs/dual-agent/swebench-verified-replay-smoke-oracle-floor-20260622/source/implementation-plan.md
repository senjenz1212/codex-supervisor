## Files / Modules To Touch

- `supervisor/swe_bench_mergeability.py`
- `supervisor/swe_bench_mergeability_cli.py`
- `tests/test_swe_bench_pro_mergeability_bridge.py`
- `docs/dual-agent/swebench-verified-replay-smoke-oracle-floor-20260622/source/tdd.md`

## Risks

- The smoke can accidentally look like a benchmark claim if Verified is not labeled plumbing-only.
- A fake or label-only oracle adapter can make the run appear official without real oracle evidence.
- Partial reviewer roster evidence can be mistaken for S_full panel quality if Slice 2 availability semantics are not reused.

## Traceability

P1 and P2 are covered by test_official_replay_smoke_writes_report_with_selected_instances. P3 is covered by test_official_replay_smoke_records_frozen_before_oracle_receipts. P4 is covered by test_verified_smoke_is_labeled_plumbing_only. P5 is covered by test_full_panel_metric_unavailable_without_full_roster. P6 is covered by test_official_replay_smoke_emits_no_policy_proposal.
