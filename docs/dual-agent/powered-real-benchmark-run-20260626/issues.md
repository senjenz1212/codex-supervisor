# Issues

Task id: `powered-real-benchmark-run-20260626`

## 1. Define the composed artifact boundary

PRD promise: P1, P2

Tracer bullet: accept a powered factorial report plus a separate all-arms diagnostic status at one checker interface.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_powered`

Allowed: one public checker returns a passed DoD verdict for a complete artifact.

Forbidden: validating only one half of the composed artifact.

## 2. Enforce raw oracle sample floors

PRD promise: P1

Tracer bullet: require `sample_size_sufficiency.status="sufficient"`, `n_good >= 30`, and `n_bad >= 30`.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_powered`

Allowed: sufficient good and bad buckets pass.

Forbidden: `n_bad=0` or `n_good=0` accepted as real scale.

## 3. Enforce paired McNemar power

PRD promise: P1

Tracer bullet: require the `full_supervisor_stack` comparison to have enough discordant pairs and a significant McNemar result.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_powered`

Allowed: `discordant_pair_count >= 25`, `mcnemar_test_passed=true`, and `p_value <= alpha`.

Forbidden: raw count sufficiency without paired power.

## 4. Require FAR/TAR/FRR confidence intervals

PRD promise: P1

Tracer bullet: check `far_tar_frr` is non-null and has confidence intervals for the all-arms diagnostic arms.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_far_and_wilson_present`

Allowed: confidence intervals with positive denominators.

Forbidden: `far_tar_frr=null` or point estimates without uncertainty blocks.

## 5. Require panel marginal evidence

PRD promise: P1

Tracer bullet: accept the all-arms `reviewer_marginal_delta_at_matched_true_accept` / `panel_marginal` block only when present.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_far_and_wilson_present`

Allowed: a panel marginal status is included in the DoD evidence.

Forbidden: a powered artifact with no panel marginal record.

## 6. Require real source provenance

PRD promise: P1

Tracer bullet: require `source_predictions_path` and reject fixture, synthetic, smoke, or gold-only path fragments.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_powered`

Allowed: a real scaled Pro predictions path.

Forbidden: `tests/fixtures`, smoke, synthetic, or `pro-gold-predictions` sources.

## 7. Preserve held-out test-pass proxy labeling

PRD promise: P2

Tracer bullet: require `benchmark_oracle.kind="swe_bench_held_out_test_pass_proxy"` and maintainer-mergeability claims disabled.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_report_only_test_pass_proxy`

Allowed: explicit test-pass proxy label.

Forbidden: calling the artifact maintainer mergeability evidence.

## 8. Preserve authority flags

PRD promise: P2

Tracer bullet: require all authority flags false across powered report, all-arms report, and nested gate flags.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_report_only_test_pass_proxy`

Allowed: report-only evidence.

Forbidden: `metric_applyable`, `policy_mutated`, or `gate_advanced` true.

## 9. Preserve evidence-conversion report-only contract

PRD promise: P2

Tracer bullet: require the powered `evidence_conversion_power_contract` to be qualified, report-only, operator-reviewed, and not policy-mutation-allowed.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_report_only_test_pass_proxy`

Allowed: qualified evidence with policy mutation disallowed.

Forbidden: evidence conversion that can mutate policy.

## 10. Fail closed on underpowered artifacts

PRD promise: P1, P2

Tracer bullet: include a negative fixture where `n_bad=0` fails with explicit reason codes.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_underpowered_artifact_is_rejected`

Allowed: blocked verdict or raised DoD error.

Forbidden: accepting underpowered artifacts as real benchmark evidence.

## 11. Record current execution truth

PRD promise: P1, P2

Tracer bullet: commit `powered-real-benchmark-execution-status.json` documenting the current blockers and next unblocked VM step.

First public-boundary RED: artifact review against the same DoD language.

Allowed: status `blocked`, authority flags false, no real benchmark claim.

Forbidden: a fake scaled report or synthetic 100-150 row corpus.
