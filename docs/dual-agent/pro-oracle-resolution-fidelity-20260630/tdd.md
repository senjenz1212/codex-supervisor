# TDD Plan

Task id: `pro-oracle-resolution-fidelity-20260630`

## Public Boundaries

- `run_swe_bench_pro_oracle(context)` for oracle scoring semantics.
- `scripts/swebench_pro_batch_driver.py::_oracle_gold_runnable(result)` for dry-gold curation.
- `build_swe_bench_pro_candidate_corpus(...)` and `summarize_swe_bench_pro_candidate_corpus(...)` for corpus disclosure.
- `swebench_mergeability_powered_factorial_runner(...)` and `assert_powered_real_benchmark_definition_of_done(...)` for powered-report disclosure.

Live Docker, solver/model execution, and reviewer panels are faked below these boundaries or left as execution evidence.

## One RED Then Minimal GREEN

RED

`tests/test_swe_bench_pro_oracle.py::test_empty_pass_to_pass_is_vacuous_pass_with_disclosure`

Expected initial failure: the current Pro oracle returns `oracle_unavailable_reason="pro_oracle_bucket_empty:pass_to_pass"` instead of `pass_to_pass_status="pass"` with disclosure.

Minimal GREEN

Change the Pro oracle's empty-bucket guard so only empty `FAIL_TO_PASS` is unavailable. Compute empty `PASS_TO_PASS` by normal set inclusion and add `pass_to_pass_empty_vacuous_pass=true` to the result and receipt.

## Next Cycles

### Cycle 2: Empty FAIL_TO_PASS still unavailable

RED: `tests/test_swe_bench_pro_oracle.py::test_empty_fail_to_pass_remains_unavailable`

GREEN: keep `pro_oracle_bucket_empty:fail_to_pass` fail-closed before status computation.

### Cycle 3: Non-empty PASS_TO_PASS regression still fails

RED: `tests/test_swe_bench_pro_oracle.py::test_nonempty_pass_to_pass_regression_still_fails`

GREEN: no special handling for non-empty `PASS_TO_PASS`; existing set-inclusion failure remains.

### Cycle 4: rc-nonzero resolved dry-gold is runnable with disclosure

RED: `tests/test_swebench_pro_batch_driver.py::test_rc_nonzero_resolved_gold_is_runnable_with_disclosure`

GREEN: `_oracle_gold_runnable` accepts parsed resolved status with non-empty tests and sets `rc_nonzero_resolved=true` when `test_command_return_code != 0`.

### Cycle 5: Disclosure reaches corpus rows and summary

RED: `tests/test_pro_corpus_generate_label.py::test_vacuous_pass_to_pass_disclosure_reaches_corpus_summary`

GREEN: `_interpret_oracle_outcome`, `build_swe_bench_pro_candidate_corpus`, `_load_official_predictions`, and `_candidate_corpus_summary_from_predictions` preserve and count disclosure flags.

### Cycle 6: Disclosure reaches powered report and DoD evidence

RED: `tests/test_powered_real_benchmark_dod.py::test_report_discloses_vacuous_and_rc_nonzero_counts`

GREEN: `swebench_mergeability_powered_factorial_runner` adds source disclosure counts and the DoD checker copies them to evidence.

### Cycle 7: Solver spend requires Phase 0 gate approval

RED: `tests/test_swebench_pro_batch_driver.py::test_solver_spend_requires_phase0_gate_decision`

GREEN: add a `phase0_gate_decision_path` / CLI flag and fail closed before solver execution unless the artifact has `solver_spend_allowed=true`.

## Refactor Check

- Keep disclosure extraction local and small; avoid duplicating receipt traversal logic in every caller.
- Keep authority flags false.
- Do not add an autonomous benchmark-to-policy bridge.
- Do not call live Docker/model/panel from unit tests.
- Do not rely on operator memory to prevent solver spend; the driver must enforce the Phase 0 gate.

## First RED Evidence

To be captured by running:

```text
.venv/bin/python -m pytest tests/test_swe_bench_pro_oracle.py::test_empty_pass_to_pass_is_vacuous_pass_with_disclosure
```
