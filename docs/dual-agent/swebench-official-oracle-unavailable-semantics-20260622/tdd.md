# TDD Plan

## Boundary First

The first RED test exercises the public official replay runner because denominator corruption appears only after adapter results are frozen into oracle rows and bridge metrics. Adapter-level tests follow only after the report boundary proves the unavailable state changes emitted behavior.

### test_official_replay_oracle_unavailable_suppresses_metrics

Maps to: Slice 1, P1, P3, P5

RED: Run `swebench_mergeability_official_replay_runner` with an adapter returning unavailable labels and assert the replay report is unavailable, metrics_unavailable_reasons includes oracle_unavailable, and FAR/TAR/FRR is absent.

GREEN: Normalize unavailable oracle outcomes in the bridge, mark all arms unavailable for affected rows, and suppress metrics with explicit unavailable reasons.

### test_official_harness_oracle_nonzero_return_is_unavailable

Maps to: Slice 1, Slice 2, P1, P4

RED: Monkeypatch the official subprocess to return a nonzero code and assert the adapter result uses unavailable labels with official_harness_failed instead of fail/fail.

GREEN: Teach the adapter failure helper to emit unavailable labels, oracle_unavailable flags, unavailable reasons, and receipt metadata.

### test_official_harness_oracle_missing_report_is_unavailable

Maps to: Slice 1, Slice 2, P1, P4

RED: Return success from the official subprocess without producing an instance report and assert the adapter result is unavailable with official_instance_report_missing.

GREEN: Route missing report detection through the same adapter failure helper rather than treating absence as a failing oracle.

### test_official_harness_oracle_valid_failure_report_stays_fail

Maps to: Slice 2, P2, P5

RED: Produce a valid official report with failing FAIL_TO_PASS tests and assert the adapter returns fail for FAIL_TO_PASS and pass for PASS_TO_PASS without oracle_unavailable.

GREEN: Keep the successful report parsing path separate from adapter failure handling and preserve official status classification.

### test_official_oracle_receipt_unavailable_requires_reason

Maps to: Slice 2, P4

RED: Validate an unavailable oracle row with missing unavailable reason and assert receipt validation reports a mismatch.

GREEN: Allow unavailable rows to have nonzero return codes or missing artifacts only when required command metadata and unavailable reason evidence exist.
