# Issues

Task id: `pro-oracle-resolution-fidelity-20260630`

## 1. Make empty PASS_TO_PASS a disclosed vacuous pass

PRD promise: P1

Tracer bullet: At the Pro oracle public boundary, a non-empty passing `FAIL_TO_PASS` bucket plus empty `PASS_TO_PASS` returns `pass/pass` with disclosure.

First public-boundary RED: `tests/test_swe_bench_pro_oracle.py::test_empty_pass_to_pass_is_vacuous_pass_with_disclosure`

Allowed: `pass_to_pass_status="pass"` and `pass_to_pass_empty_vacuous_pass=true`.

Forbidden: `oracle_unavailable` for empty `PASS_TO_PASS`, or hidden vacuous pass.

## 2. Preserve empty FAIL_TO_PASS fail-closed behavior

PRD promise: P2

Tracer bullet: At the same Pro oracle boundary, empty `FAIL_TO_PASS` remains unavailable.

First public-boundary RED: `tests/test_swe_bench_pro_oracle.py::test_empty_fail_to_pass_remains_unavailable`

Allowed: unavailable reason `pro_oracle_bucket_empty:fail_to_pass`.

Forbidden: accepting empty `FAIL_TO_PASS` or labeling from regression tests alone.

## 3. Preserve non-empty PASS_TO_PASS regression failure

PRD promise: P3

Tracer bullet: A non-empty regression bucket with a failed or missing test yields `pass_to_pass_status="fail"`.

First public-boundary RED: `tests/test_swe_bench_pro_oracle.py::test_nonempty_pass_to_pass_regression_still_fails`

Allowed: regression failure remains oracle-bad.

Forbidden: treating non-empty `PASS_TO_PASS` as vacuous.

## 4. Accept rc-nonzero dry-gold only when parsed statuses resolve

PRD promise: P4

Tracer bullet: The dry-gold curation predicate accepts rc-nonzero resolved gold and records disclosure.

First public-boundary RED: `tests/test_swebench_pro_batch_driver.py::test_rc_nonzero_resolved_gold_is_runnable_with_disclosure`

Allowed: runnable with `rc_nonzero_resolved=true`.

Forbidden: accepting rc-nonzero with empty tests, failed patch apply, or failed parsed status.

## 5. Keep rc-nonzero failure cases fail-closed

PRD promise: P4

Tracer bullet: The curation predicate still rejects rc-nonzero when parser/tests/status preconditions do not hold.

First public-boundary RED: extend `tests/test_swebench_pro_batch_driver.py::test_rc_nonzero_resolved_gold_is_runnable_with_disclosure` with negative cases or add a companion negative test.

Allowed: explicit missing-check reasons.

Forbidden: broad rc bypass.

## 6. Propagate disclosure through interpreted oracle outcomes

PRD promise: P5

Tracer bullet: `_interpret_oracle_outcome` copies disclosure flags from raw outcome or receipt.

First public-boundary RED: `tests/test_pro_corpus_generate_label.py::test_vacuous_pass_to_pass_disclosure_reaches_corpus_summary`

Allowed: interpreted outcomes expose the two disclosure booleans.

Forbidden: disclosure lost before corpus rows are written.

## 7. Write disclosure fields into prediction rows

PRD promise: P5

Tracer bullet: `build_swe_bench_pro_candidate_corpus` writes row-level disclosure fields when the oracle reports them.

First public-boundary RED: `tests/test_pro_corpus_generate_label.py::test_vacuous_pass_to_pass_disclosure_reaches_corpus_summary`

Allowed: JSONL rows include `pass_to_pass_empty_vacuous_pass` / `rc_nonzero_resolved` only when true.

Forbidden: row-level omission.

## 8. Count disclosure fields in corpus summaries

PRD promise: P5

Tracer bullet: `summarize_swe_bench_pro_candidate_corpus` returns disclosure counts.

First public-boundary RED: `tests/test_pro_corpus_generate_label.py::test_vacuous_pass_to_pass_disclosure_reaches_corpus_summary`

Allowed: summary has `vacuous_pass_to_pass_count` and `rc_nonzero_resolved_count`.

Forbidden: summaries that hide weaker evidence composition.

## 9. Carry disclosure counts into powered report metadata

PRD promise: P5

Tracer bullet: `swebench_mergeability_powered_factorial_runner` includes disclosure counts from source predictions.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_report_discloses_vacuous_and_rc_nonzero_counts`

Allowed: powered report metadata contains both counts.

Forbidden: powered report lacks disclosure counts while source rows contain them.

## 10. Include disclosure counts in DoD evidence

PRD promise: P5

Tracer bullet: The powered real benchmark DoD checker surfaces counts in its evidence block.

First public-boundary RED: `tests/test_powered_real_benchmark_dod.py::test_report_discloses_vacuous_and_rc_nonzero_counts`

Allowed: DoD evidence includes counts while preserving report-only labels.

Forbidden: a passed DoD verdict with hidden disclosure counts.

## 11. Mechanically block solver spend without Phase 0 approval

PRD promise: P6, P7

Tracer bullet: The batch driver refuses `--run-solver` unless a Phase 0 gate decision artifact explicitly says `solver_spend_allowed=true`.

First public-boundary RED: `tests/test_swebench_pro_batch_driver.py::test_solver_spend_requires_phase0_gate_decision`

Allowed: dry curation without spend artifact; solver spend only with explicit passed gate artifact.

Forbidden: `--allow-live --run-solver` bypassing Phase 0 gate approval.

## 12. Rerun Phase 0 only and record the gate decision

PRD promise: P6

Tracer bullet: After focused tests pass, rerun dry-gold curation on the existing 108-record pool and commit evidence.

First public-boundary RED: execution artifact review against `curated-roster.json` and `phase0-gate-decision.json`; no unit test runs Docker.

Allowed: proceed-to-operator-review if prereg reliability bar passes, or blocked evidence if it fails.

Forbidden: solver/model spend, prereg bar lowering, or a powered benchmark claim.
