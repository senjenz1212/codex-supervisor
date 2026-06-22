## Files / Modules To Touch

- `supervisor/swe_bench_mergeability_cli.py`
- `supervisor/swe_bench_mergeability.py`
- `tests/test_swe_bench_pro_mergeability_bridge.py`
- `docs/dual-agent/swebench-official-oracle-runner-filtered-smoke-20260622/source/issues.md`
- `docs/dual-agent/swebench-official-oracle-runner-filtered-smoke-20260622/source/tdd.md`
- `docs/dual-agent/swebench-official-oracle-runner-filtered-smoke-20260622/source/implementation-plan.md`

## Risks

- The official adapter could remain a descriptive label unless tests prove command execution and receipt output at the CLI boundary.
- Unsupported adapter-kind labels could bypass receipt validation unless the allowlist is enforced at both the CLI and runner seams.
- Invalid official evidence could be labeled unavailable while stale FAR/TAR metrics still leak through nested reports or the CLI summary.
- Filtering could happen too late and leave smoke runs coupled to predictions for unselected instances.
- Hidden oracle fields could leak through public packets if the replay manifest is passed through without a structural scrub.
- Docker or official harness availability may vary locally, so tests need fake official adapters while production reports preserve unavailable states honestly.

## Traceability

P1 is covered by test_official_replay_cli_requires_oracle_adapter_before_metrics, test_official_replay_cli_rejects_unknown_adapter_kind_before_metrics, test_official_replay_runner_rejects_unknown_adapter_kind_before_metrics, test_official_replay_cli_passes_fake_runner_and_writes_report, test_official_replay_label_only_adapter_receipt_is_unavailable, and test_official_replay_cli_suppresses_metrics_when_oracle_proof_invalid.
P2 is covered by test_instance_id_filtering_happens_before_prediction_coverage and test_limit_filtering_is_deterministic_and_reported.
P3 is covered by test_oracle_receipts_are_after_frozen_decisions_and_hide_oracle_fields.
P4 is covered by test_official_replay_cli_passes_fake_runner_and_writes_report, test_official_replay_label_only_adapter_receipt_is_unavailable, test_official_replay_cli_suppresses_metrics_when_oracle_proof_invalid, test_oracle_receipts_are_after_frozen_decisions_and_hide_oracle_fields, and test_official_equivalent_label_validation_failure_is_unavailable.
P5 is covered by test_official_replay_cli_requires_oracle_adapter_before_metrics, test_official_replay_cli_rejects_unknown_adapter_kind_before_metrics, test_official_replay_runner_rejects_unknown_adapter_kind_before_metrics, test_official_replay_label_only_adapter_receipt_is_unavailable, test_official_replay_cli_suppresses_metrics_when_oracle_proof_invalid, and test_official_equivalent_label_validation_failure_is_unavailable.
