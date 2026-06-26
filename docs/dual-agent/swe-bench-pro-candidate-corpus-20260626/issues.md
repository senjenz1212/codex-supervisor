# Issues

Task id: `swe-bench-pro-candidate-corpus-20260626`

## 1. Preserve Candidate Provenance In Prediction Loading

PRD promise

P1

Public boundary

`_load_official_predictions(...)`.

Chosen seam

JSONL row -> loaded prediction candidate mapping.

First RED test

`tests/test_swe_bench_pro_candidate_corpus.py::test_load_predictions_accepts_good_and_bad_with_provenance`.

Allowed outcomes

Rows with good and bad labels keep `origin`, `producer`, `candidate_artifact_hash`, `model_patch_sha256`, and baseline receipt keys.

Forbidden outcomes

Dropping provenance, accepting empty patches, or silently omitting candidate hashes.

## 2. Compute Stable Candidate Hashes

PRD promise

P1

Public boundary

The same loader boundary and generated corpus JSONL.

Chosen seam

Hash helper over instance id, candidate id, origin, producer, and model patch hash.

First RED test

Extend the loader test to omit one hash and assert the loader computes a stable 64-character hash.

Allowed outcomes

Explicit hashes are preserved and missing hashes are computed deterministically.

Forbidden outcomes

Random hashes, timestamp-derived hashes, or duplicate hashes within one corpus.

## 3. Summarize FAR Denominator From Corpus Labels

PRD promise

P1

Public boundary

Corpus summary over loaded prediction rows.

Chosen seam

Loaded predictions -> `oracle-good`/`oracle-bad` counts.

First RED test

`tests/test_swe_bench_pro_candidate_corpus.py::test_far_denominator_nonzero_with_oracle_bad`.

Allowed outcomes

A fixture corpus with at least one oracle-bad row yields `n_bad > 0` and a non-degenerate false-accept denominator.

Forbidden outcomes

Reporting FAR-capable when all rows are good or unlabeled.

## 4. Build Corpus Rows From Oracle-Labeled Attempts

PRD promise

P1, P2

Public boundary

Candidate-corpus builder function.

Chosen seam

Generated attempts plus oracle results -> output JSONL rows.

First RED test

`tests/test_swe_bench_pro_candidate_corpus.py::test_generator_bins_nonresolving_as_oracle_bad`.

Allowed outcomes

Pass/pass attempts become `oracle-good`; fail/pass or pass/fail attempts with patch-applied evidence become `oracle-bad`.

Forbidden outcomes

Using solver self-report or public test status as the label.

## 5. Exclude Non-Applying Attempts

PRD promise

P2

Public boundary

Candidate-corpus builder report.

Chosen seam

Oracle receipt/status classification.

First RED test

Add a non-applying oracle result to the generator fixture and assert it is excluded with reason `non_applying_patch`.

Allowed outcomes

Non-applying attempts are recorded in the report but not written to predictions JSONL.

Forbidden outcomes

Counting non-applying patches as oracle-bad.

## 6. Exclude Unavailable Oracle Attempts

PRD promise

P2

Public boundary

Candidate-corpus builder report.

Chosen seam

Oracle `unavailable` status handling.

First RED test

Add an unavailable oracle result to the generator fixture and assert it is excluded with the real unavailable reason.

Allowed outcomes

Unavailable candidates are auditable but never part of FAR denominator.

Forbidden outcomes

Treating Docker, timeout, parser, or ENOSPC failures as bad candidates.

## 7. Preserve Optional Baseline Receipts

PRD promise

P1

Public boundary

`_load_official_predictions(...)` and output JSONL.

Chosen seam

`PRODUCED_BASELINE_RECEIPT_KEYS`.

First RED test

The loader fixture includes `single_agent_baseline_decision` and expects it to survive loading.

Allowed outcomes

Known baseline receipt keys are copied unchanged.

Forbidden outcomes

Moving receipt data into hidden sidecars or dropping it.

## 8. Record Blocked Real Execution Honestly

PRD promise

P1, P2

Public boundary

Packet artifact under `artifacts/`.

Chosen seam

Host capability probe plus real execution attempt record.

First RED test

Not a unit test. Acceptance is a committed artifact that records real blockers when the corpus cannot be produced.

Allowed outcomes

Blocked artifact with exact Docker/disk/solver/oracle blockers and all authority flags false.

Forbidden outcomes

Writing a fake `pro-predictions.jsonl` to satisfy `n_bad > 0`.

## 9. Keep Oracle Isolation

PRD promise

P2

Public boundary

Generator and loader artifacts.

Chosen seam

Only oracle result labels and receipts are written after oracle execution.

First RED test

The generator fixture asserts labels come from oracle status only.

Allowed outcomes

No hidden FAIL_TO_PASS/PASS_TO_PASS content is needed by the public loader.

Forbidden outcomes

Leaking hidden oracle fields into public reviewer/generator inputs.

## 10. Keep Authority Flags False

PRD promise

P1, P2

Public boundary

Corpus report and packet ledger.

Chosen seam

Report construction.

First RED test

The generated/blocked report fixture asserts all authority flags are false.

Allowed outcomes

Report-only evidence with no metric or policy authority.

Forbidden outcomes

`metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, or `gate_advanced` becoming true.

## 11. Commit Source And Tests Before No-Mistakes

PRD promise

P1, P2

Public boundary

Git history and no-mistakes preflight.

Chosen seam

Focused source/test commit followed by validation.

First RED test

No unit test. Acceptance is a committed source/test diff before no-mistakes runs.

Allowed outcomes

Source and tests are committed together; packet docs/artifacts are committed explicitly; scratch remains ignored.

Forbidden outcomes

Running no-mistakes against a dirty worktree or bundling scratch/transcript noise.
