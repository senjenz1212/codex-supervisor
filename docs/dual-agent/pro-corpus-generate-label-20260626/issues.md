# Issues

Task id: `pro-corpus-generate-label-20260626`

## 1. Gather dependency evidence before execution

PRD promise

P1, P2, P3

Public boundary

Packet artifact under `artifacts/`.

Chosen seam

Dependency artifacts from oracle, solver, and run-script slices.

First RED test

No unit test. Acceptance is an execution-status artifact citing dependency readiness.

Allowed outcomes

The artifact records whether the gold proof, solver, and run-script dependencies are usable for corpus generation.

Forbidden outcomes

Ignoring a dependency blocker and emitting a fake corpus.

## 2. Convert solver output into candidate attempts

PRD promise

P1

Public boundary

`solver_attempts_for_candidate_corpus(...)`.

Chosen seam

Generator output JSON with an `attempts` array.

First RED test

Use existing solver tests plus `test_corpus_bins_good_and_bad_with_provenance`.

Allowed outcomes

Every solver attempt is retained with its own patch, hash, origin, producer, and baseline receipt.

Forbidden outcomes

Keeping only the first attempt or dropping rejected attempts.

## 3. Add gold reference patch backstops

PRD promise

P1

Public boundary

Attempt list passed to `build_swe_bench_pro_candidate_corpus(...)`.

Chosen seam

Dataset reference patch represented as an attempt with `origin.kind="dataset_reference_patch"`.

First RED test

`tests/test_pro_corpus_generate_label.py::test_corpus_bins_good_and_bad_with_provenance`.

Allowed outcomes

Gold attempts become oracle-good only after the Pro oracle returns `pass/pass` and `patch_applied=true`.

Forbidden outcomes

Marking gold as good without oracle labeling or presenting gold rows as model-generated attempts.

## 4. Label attempts through the Pro oracle

PRD promise

P1, P3

Public boundary

`build_swe_bench_pro_candidate_corpus(...)`.

Chosen seam

`oracle_runner(attempt)` below the public builder seam.

First RED test

`tests/test_pro_corpus_generate_label.py::test_corpus_bins_good_and_bad_with_provenance`.

Allowed outcomes

The builder writes `oracle-good` and `oracle-bad` rows according to oracle outcomes.

Forbidden outcomes

Using solver accept/reject, public tests, or reviewer output as oracle labels.

## 5. Preserve hashes and provenance in JSONL

PRD promise

P1

Public boundary

`_load_official_predictions(...)`.

Chosen seam

Written JSONL rows consumed by official replay.

First RED test

`tests/test_pro_corpus_generate_label.py::test_corpus_bins_good_and_bad_with_provenance`.

Allowed outcomes

Rows preserve `candidate_artifact_hash`, `model_patch_sha256`, `diff_sha256`, `origin`, `producer`, and baseline receipts.

Forbidden outcomes

Hash mismatch, duplicate candidate hashes, or dropped provenance.

## 6. Keep FAR non-degenerate only when `n_bad > 0`

PRD promise

P2

Public boundary

Corpus summary.

Chosen seam

`summarize_swe_bench_pro_candidate_corpus(...)`.

First RED test

`tests/test_pro_corpus_generate_label.py::test_n_bad_nonzero_makes_far_real`.

Allowed outcomes

At least one oracle-bad row gives `false_accept_denominator >= 1` and `far_degenerate=false`.

Forbidden outcomes

Gold-only corpus reported as FAR-capable.

## 7. Exclude non-applying attempts

PRD promise

P3

Public boundary

Candidate-corpus builder report.

Chosen seam

Oracle outcome interpretation plus `patch_applied is True` gate.

First RED test

`tests/test_pro_corpus_generate_label.py::test_nonresolving_only_counts_when_patch_applied`.

Allowed outcomes

Failed oracle outcomes count as `oracle-bad` only when the patch applied.

Forbidden outcomes

Counting patch-apply failures in `n_bad`.

## 8. Exclude unavailable oracle attempts

PRD promise

P3

Public boundary

Candidate-corpus builder report.

Chosen seam

Oracle unavailable status and unavailable reason.

First RED test

Covered by the builder fixture and existing candidate-corpus tests.

Allowed outcomes

Unavailable attempts are recorded in `excluded` and omitted from JSONL.

Forbidden outcomes

Treating Docker, parser, timeout, missing-script, or empty-parser failures as oracle-bad.

## 9. Record blocked execution honestly

PRD promise

P1, P2

Public boundary

`docs/dual-agent/pro-corpus-generate-label-20260626/artifacts/corpus-generate-label-execution-status.json`.

Chosen seam

Host and dependency probe plus artifact manifest.

First RED test

No unit test. Acceptance is a committed artifact with exact blockers.

Allowed outcomes

If no real corpus is produced, the artifact states `status="blocked"` or `status="unavailable"` and `produced_predictions_path=""`.

Forbidden outcomes

Writing a fixture `pro-predictions.jsonl` to satisfy the seed target.

## 10. Keep authority flags false

PRD promise

P2

Public boundary

Report and execution artifact authority flags.

Chosen seam

The builder report and packet ledger.

First RED test

Assert the builder report keeps all authority flags false in `test_corpus_bins_good_and_bad_with_provenance`.

Allowed outcomes

All authority flags remain false.

Forbidden outcomes

Metric, improvement, default-change, policy, or gate-advanced authority becomes true.

## 11. Preserve oracle isolation

PRD promise

P1, P3

Public boundary

Attempt rows and Pro oracle adapter context.

Chosen seam

Attempts carry public patch/provenance; hidden oracle fields are supplied only to `run_swe_bench_pro_oracle` after freeze.

First RED test

Covered by existing solver public-packet tests and this slice's builder tests.

Allowed outcomes

Unit tests fake oracle results below the public builder seam; real execution uses the Pro oracle.

Forbidden outcomes

Solver sees hidden oracle fields or unit tests mock the public outcome itself.
