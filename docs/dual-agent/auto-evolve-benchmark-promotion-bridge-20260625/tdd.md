# Auto-Evolve Benchmark Promotion Bridge TDD Contracts

Use one RED -> one GREEN -> repeat. First RED tests must hit public boundaries or chosen seams; helper-only tests can follow only after the boundary test exists.

## AEB-0 TDD Contract

### Cycle 1

RED: `tests/test_swe_bench_pro_mergeability_bridge.py::test_missing_real_all_arms_artifact_blocks_downstream_readiness`

Boundary: `swebench_mergeability_official_all_arms_diagnostic_runner` or a small AEB-0 readiness wrapper around that boundary.

Expected failure before GREEN: downstream bridge readiness can be discussed without a real or blocked AEB-0 artifact.

GREEN target: return/write `status=blocked`, `blocked_reasons=["real_official_all_arms_artifact_required"]`, and false authority flags when no AEB-0 artifact exists.

### Cycle 2

RED: `tests/test_swe_bench_pro_mergeability_bridge.py::test_aeb0_missing_cli_prerequisites_write_blocked_artifact`

Boundary: official all-arms CLI/preflight wrapper.

Expected failure before GREEN: missing `--allow-dataset-fetch`, dataset, predictions, oracle adapter, or supported adapter kind exits before any blocked AEB-0 artifact/ledger event exists.

GREEN target: emit a blocked AEB-0 artifact or ledger event for pre-artifact CLI blockers.

### Cycle 3

RED: `tests/test_swe_bench_pro_mergeability_bridge.py::test_aeb0_missing_baseline_or_oracle_receipt_blocks`

Boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.

Expected failure before GREEN: real-input-shaped artifact with missing produced baseline receipt or official oracle receipt can look like real evidence.

GREEN target: artifact status is unavailable/blocked with exact baseline/oracle reasons and all authority flags false.

### Cycle 4

RED: `tests/test_swe_bench_pro_mergeability_bridge.py::test_aeb0_verified_dataset_is_smoke_only`

Boundary: official all-arms diagnostic report.

Expected failure before GREEN: Verified dataset report can satisfy serious real-benchmark readiness.

GREEN target: dataset readiness distinguishes SWE-bench Pro or held-out equivalent from Verified smoke.

## AEB-1 TDD Contract

### Cycle 1

RED: `tests/test_swe_bench_official_oracle.py::test_official_oracle_empty_present_buckets_can_pass`

Boundary: `run_official_harness_oracle` with fake official report artifact parsing.

Expected failure before GREEN: oracle bucket handling does not explicitly encode empty-present semantics.

GREEN target: empty-but-present `FAIL_TO_PASS` or `PASS_TO_PASS` bucket with no failures remains pass-compatible.

### Cycle 2

RED: `tests/test_swe_bench_official_oracle.py::test_official_oracle_missing_or_malformed_status_bucket_is_unavailable`

Boundary: `run_official_harness_oracle` with fake official report artifact parsing.

Expected failure before GREEN: `_status_for` currently treats a missing or non-mapping `FAIL_TO_PASS` or `PASS_TO_PASS` bucket as `pass`.

GREEN target: missing or malformed official instance status buckets become unavailable/blocked receipts with exact reason.

### Cycle 3

RED: hidden oracle/protected material injected into public candidate, reviewer packet, generator input, frozen decision, or public packet surface must appear in hidden leak/blocked reasons and never reach public packet fields.

GREEN target: extend public packet/report summary only; do not scan hidden oracle manifests as public leaks.

## AEB-2 TDD Contract

### Cycle 1

RED: `tests/test_mergeability_bench.py::test_powered_factorial_requires_mde_alpha_power_and_ci_width_for_evidence_conversion`

Boundary: `run_powered_factorial_mergeability_evaluation`.

Expected failure before GREEN: sufficient `min_good`/`min_bad` alone may leave the report looking conversion-ready.

GREEN target: add `evidence_conversion_power_contract` with target MDE, alpha, power, CI width, paired discordance, AEB-0 dependency status, and blocked/underpowered status.

### Cycle 2

RED: paired discordance is zero or missing while raw counts are high; evidence-conversion contract stays blocked.

GREEN target: gate evidence-conversion readiness on paired/discordant evidence.

## AEB-3 TDD Contract

### Cycle 1

RED: `tests/test_mergeability_bench.py::test_reviewer_independence_disagreement_without_oracle_errors_is_not_effective_vote`

Boundary: `run_mergeability_reviewer_roster_diagnostic`.

Expected failure before GREEN: report or linkage might be read as independence from disagreement.

GREEN target: preserve `effective_vote_estimate.status=unavailable` and guard reasons through linked official/powered evidence.

### Cycle 2

RED: `tests/test_mergeability_bench.py::test_reviewer_measurement_gate_cannot_change_trust_path_flags`

Boundary: reviewer-roster diagnostic report and linked official/powered report.

Expected failure before GREEN: roster/effective-vote measurement could alter applyability or policy authority.

GREEN target: measurement output cannot change `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, `gate_advanced`, roster authority, or policy derivation.

## AEB-4 TDD Contract

### Cycle 1

RED: `tests/test_autoresearch_benchmark_promotion.py::test_benchmark_backed_evidence_conversion_blocks_without_aeb0_or_evaluator_hash`

Boundary: new seam `promote_benchmark_report_to_autoresearch_report`.

Expected failure before GREEN: no bridge exists, and accepted-path bridge tests do not require AEB-0-qualified evidence.

GREEN target: create bridge interface and return blocked AutoResearch report/ledger record for missing AEB-0 artifact ref or evaluator hash.

### Cycle 2

RED: powered benchmark report with AEB-0/evaluator hash but missing empty-floor comparison is rejected and not derivable.

GREEN target: bridge emits rejected/blocked record with exact reason and mutation flags false.

### Cycle 3

RED: complete powered benchmark + AEB-0-qualified evidence + quality controls + empty-floor win + positive delta + overlay candidate ref yields accepted report-only AutoResearch record through AutoResearch validation/report shape.

GREEN target: accepted record is report-only, operator-review-required, and policy derivation sees it as derivable without mutation.

## AEB-5 TDD Contract

### Cycle 1

RED: `tests/test_autoresearch_policy_evolution.py::test_bridge_record_without_aeb0_or_empty_floor_writes_derivation_skip`

Boundary: `derive_policy_evolution_proposals_from_report`.

Expected failure before GREEN: skipped reason may not be specific or ledger-visible for bridge-created records.

GREEN target: write/propagate derivation skipped record with exact applyability reason.

### Cycle 2

RED: accepted bridge-created AutoResearch record creates draft proposal with authority invariants and candidate overlay ref.

GREEN target: derive proposal only; do not call approval/apply.

## AEB-6 TDD Contract

### Cycle 1

RED: `tests/test_auto_evolve_benchmark_ledger.py::test_blocked_downstream_ledger_record_without_aeb0_preserves_false_authority_flags`

Boundary: `supervisor_event_ledger` or new ledger writer seam.

Expected failure before GREEN: no schema/writer exists for `supervisor-auto-evolve-benchmark-ledger/v1`, and no AEB-0 dependency status is recorded.

GREEN target: append one blocked JSONL event with required schema fields and `blocked_reasons=["real_official_all_arms_artifact_required"]`.

### Cycle 2

RED: translation audit fails when a PRD promise lacks an issue, a first RED test is helper-only, or a real-evidence claim lacks AEB-0.

GREEN target: add audit helper/report that fails closed before implementation launch.

### Cycle 3

RED: `tests/test_auto_evolve_benchmark_ledger.py::test_observability_sink_cannot_enter_trust_path`

Boundary: future read-only sink adapter contract.

Expected failure before GREEN: sink deferral is not represented in ledger/audit contracts.

GREEN target: ledger/audit records sink deferred status and rejects sink-origin authority flags as trust-path evidence.

## Public Boundary Notes

- `supervisor_event_ledger` is an existing local public testing boundary in `docs/testing/public-boundaries.md`.
- `swebench_mergeability_official_all_arms_diagnostic_runner` is the AEB-0 root artifact interface.
- For new bridge functionality, `promote_benchmark_report_to_autoresearch_report` should be a deep module interface: small input contract, rich validation/reporting behind it.
- Live Docker, live SWE-bench fetch, live reviewer panel, observability sink, and live model calls stay mocked below the boundary in automated tests.
