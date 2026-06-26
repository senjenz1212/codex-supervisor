# TDD Plan

Task id: `swe-bench-pro-candidate-corpus-20260626`

## Public Boundary

The first implementation proof is `_load_official_predictions(...)`, because official replay already consumes predictions through that loader. The generator proof then uses a candidate-corpus builder at the public generator/binning seam. Solver calls, Docker, and the Slice-1 Pro oracle are faked below that seam in unit tests; real execution remains packet artifact evidence.

## Cycle 1: Loader Preserves Good/Bad Provenance

RED

Add `tests/test_swe_bench_pro_candidate_corpus.py::test_load_predictions_accepts_good_and_bad_with_provenance`. It writes a JSONL fixture with one `oracle-good` and one `oracle-bad` row, each with provenance and hashes, and asserts `_load_official_predictions(...)` preserves them.

Minimal GREEN

Extend the loader to copy known provenance fields, compute missing hashes deterministically, and keep existing baseline receipt behavior.

## Cycle 2: FAR Denominator Is Nonzero With Oracle-Bad

RED

Add `tests/test_swe_bench_pro_candidate_corpus.py::test_far_denominator_nonzero_with_oracle_bad`. It loads a fixture corpus with at least one `oracle-bad` row and asserts the corpus summary reports `n_bad > 0`.

Minimal GREEN

Add a small report-only corpus summary helper that counts `oracle-good` and `oracle-bad` labels from loaded predictions and marks FAR as non-degenerate only when `n_bad > 0`.

## Cycle 3: Generator Bins Nonresolving Attempts As Oracle-Bad

RED

Add `tests/test_swe_bench_pro_candidate_corpus.py::test_generator_bins_nonresolving_as_oracle_bad`. It passes three generated attempts through a fake oracle below the builder seam: one pass/pass, one patch-applies fail/pass, and one unavailable. The test expects only the first two to be written, with the second labeled `oracle-bad`.

Minimal GREEN

Implement the candidate-corpus builder and oracle label classifier. Preserve origin, producer, model patch hash, candidate artifact hash, and exclusion reasons.

## Cycle 4: Non-Applying Patches Stay Out Of `n_bad`

RED

Extend the generator test with an oracle result carrying `patch_applied=false` and failed tests. Assert it is excluded and does not increase `n_bad`.

Minimal GREEN

Require explicit patch-applied evidence or a completed scoring result before classifying a failing oracle result as `oracle-bad`.

## Cycle 5: Blocked Execution Artifact

RED

Not a unit test. Before real execution, the packet lacks a current artifact showing whether this host can run solver attempts and the Pro oracle.

Minimal GREEN

Write a packet artifact that records the real execution status. If Docker/solver/disk is unavailable, record blocked status and do not emit a FAR-capable `pro-predictions.jsonl`.

## Refactor Check

- Keep loader changes local to the official predictions seam.
- Keep generator/binning logic behind one small builder interface.
- Do not change FAR math, reviewer panels, or policy evolution.
- Do not silently mock live solver or oracle evidence.

## Accepted Ledger Record

```jsonl
{"stage":"tdd_review","task_id":"swe-bench-pro-candidate-corpus-20260626","status":"accepted","first_red":"tests/test_swe_bench_pro_candidate_corpus.py::test_load_predictions_accepts_good_and_bad_with_provenance","evidence":["docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/tdd.md","docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/grill-findings-tdd.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
