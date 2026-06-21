# TDD Plan

## Public Boundary

`run_fixture_panel_produced_baseline_measurement` is the first public boundary. Tests must call this runner with fixture reviewer adapters and inspect the persisted `paired_acceptance_report.json`.

## RED -> GREEN Steps

1. RED: `test_fixture_measurement_runner_writes_primary_single_agent_comparison` calls the runner and expects an exported report with `primary_comparison_name=supervisor_vs_single_agent_baseline`, available `supervisor_full_gate`, available `single_agent_baseline`, and metadata baseline marked non-primary. GREEN: add the runner, baseline receipt builder, and primary comparison report fields.
2. RED: `test_fixture_measurement_runner_records_panel_rationales_and_packet_refs` expects every row to expose packet hashes and panel decisions, while panel-invoked rows expose reviewer ids and rationale evidence. GREEN: preserve reviewer rationale fields from independent reviewer results and mirror them into row-level full-gate evidence.
3. RED: public-floor rejects must be available full-gate rejects with `public_review_rejected`, while public-floor accepts still require real reviewer verdicts and rationales. GREEN: short-circuit only after S_probe rejects and keep reviewer requirements for public accepts.
4. RED: `test_fixture_measurement_runner_rejects_unavailable_configured_panel` invokes the runner with unavailable configured reviewers and expects a `MergeabilityBenchError`. GREEN: validate full-gate and baseline availability after the paired report returns.
5. RED: `test_fixture_measurement_runner_remains_report_only` asserts `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, `gate_advanced`, and policy derivation all stay false. GREEN: keep the existing authority flags and add no policy evolution path.

## Guardrails

Do not write all tests before implementation if a test reveals a better public seam. Keep helpers private unless the public runner needs a stable input contract. Do not use oracle labels to build baseline receipts. Do not make fake reviewers satisfy a real measurement artifact silently; injected reviewers are test substitutes, while production default options must use the configured reviewer roster.
