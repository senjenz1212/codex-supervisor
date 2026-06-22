## Files / Modules to touch

- supervisor/swe_bench_mergeability.py: add the official replay adapter, replay oracle hook, report wrapper, and constants.
- supervisor/swe_bench_mergeability_cli.py: expose official replay flags and opt-in refusal.
- tests/test_swe_bench_pro_mergeability_bridge.py: add public-boundary coverage for the official replay interface and CLI guard.
- pyproject.toml: add an optional datasets dependency group if the lazy loader needs an install target.
- validate_official_replay_records will stay inside the adapter seam, while run_official_replay and parse_official_predictions stay observable through public tests.

## Risks

- Hidden oracle leakage is the dominant risk because official rows carry FAIL_TO_PASS, PASS_TO_PASS, patch, and test_patch together. Tests must assert absence at public packet, reviewer packet, and frozen decision surfaces.
- A deterministic equivalent oracle can be mistaken for Docker. The report must label oracle_adapter_kind and keep all policy/improvement switches false.
- Adding another report shape could fork metric semantics. The adapter must delegate FAR/TAR aggregation to the existing replay runner and bridge report.

## Traceability

- P1 -> test_official_replay_refuses_dataset_fetch_without_opt_in and test_official_replay_cli_refuses_without_opt_in.
- P2 -> test_official_replay_materializes_public_bundle_and_excludes_hidden_oracle.
- P3 -> test_official_replay_freezes_decisions_before_oracle_adapter.
- P4 -> test_official_replay_report_labels_oracle_adapter_and_stays_report_only.
