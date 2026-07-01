# Tri-Agent Findings

Task id: `pro-oracle-resolution-fidelity-20260630`

Status: accepted with required changes folded into PRD/TDD scope.

## Validator A: Upstream Fidelity

Verdict: block before solver spend until oracle semantics are fixed.

Findings:

- `supervisor/swe_bench_official_oracle.py:638-643` treats both empty `fail_to_pass` and empty `pass_to_pass` as unavailable. The `pass_to_pass` branch is the local divergence.
- `supervisor/swe_bench_official_oracle.py:662-663` already has the correct set-inclusion scoring shape for preserving non-empty regression failures.
- Disclosure should include counts, not hidden test names: `fail_to_pass_count`, `pass_to_pass_count`, and `pass_to_pass_empty_vacuous_pass`.

Folded changes:

- P1/P2/P3 keep empty `PASS_TO_PASS` separate from empty `FAIL_TO_PASS`.
- TDD now includes explicit receipt disclosure and non-empty regression failure.

## Validator B: Disclosure Integrity

Verdict: not ready for Phase 0 rerun until disclosure fields survive every reporting boundary.

Findings:

- `_interpret_oracle_outcome` at `supervisor/swe_bench_mergeability.py:417` currently drops disclosure fields.
- `build_swe_bench_pro_candidate_corpus` at `supervisor/swe_bench_mergeability.py:5023` writes rows without disclosure fields.
- `_load_official_predictions` and `OFFICIAL_PREDICTION_METADATA_KEYS` around `supervisor/swe_bench_mergeability.py:111` and `:4471` preserve only a narrow metadata set.
- `_candidate_corpus_summary_from_predictions` at `supervisor/swe_bench_mergeability.py:4511` reports only count/good/bad/unlabeled.
- `_normalise_oracle_adapter_outcome`, oracle output rows, combined oracle outcomes, powered report metadata, and DoD evidence also need preservation/counts.
- `scripts/pro_oracle_gold_proof_runner.py` still applies stricter historical proof checks; either update it or clearly de-scope it from Phase 0 gate evidence.

Folded changes:

- P5 explicitly requires row, loader, bridge, powered report, and DoD propagation.
- Implementation will update the gold-proof runner only to keep operator evidence semantics aligned; it will not run live proof here.

## Validator C: Scale Gate Honesty

Verdict: needs changes before spend.

Findings:

- `_oracle_gold_runnable` at `scripts/swebench_pro_batch_driver.py:174-193` currently requires `test_command_return_code == 0`.
- The batch driver writes `curated-roster.json` and can immediately run solver when `--run-solver` is passed; `--allow-live` is not the same as a passed Phase 0 prereg gate.
- The rerun evidence should include `batch-driver-manifest.json` showing `run_solver=false`, `run_labeling=false`, and `run_powered=false`, plus curated roster counts and excluded-reason distribution.

Folded changes:

- P4 requires parsed-status acceptance with `rc_nonzero_resolved` disclosure.
- P6 now includes a hard solver-spend interlock: solver/model phases fail closed unless a Phase 0 gate artifact explicitly allows spend.
