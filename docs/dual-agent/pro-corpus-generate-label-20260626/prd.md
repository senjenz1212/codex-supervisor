# Pro Corpus Generate And Label PRD

Task id: `pro-corpus-generate-label-20260626`

Depends on: `pro-oracle-gold-proof-20260626`, `pro-single-agent-solver-20260626`, `pro-runscripts-coverage-20260626`

## Problem Statement

The real SWE-bench Pro benchmark still cannot measure false-accept rate from a gold-only predictions file. The shared mergeability report uses oracle-bad candidates as the false-accept denominator, and `_rate(...)` returns `0.0` when that denominator is zero. That makes a gold-only corpus look numerically safe while proving nothing about false accepts.

The prior candidate-corpus slice added the loader and binning primitives, but it did not produce a real FAR-capable corpus. The current dependency artifacts prove one oracle-good gold patch and run-script coverage, while the single-agent solver dependency is still blocked for live model execution. This slice must either produce a real `pro-predictions.jsonl` from real k>1 solver attempts plus gold backstops, or record an honest blocked execution artifact.

## Solution

Assemble generated solver attempts from the Slice-2 generator, append dataset reference-patch attempts as oracle-good backstops, and label every applying candidate through `build_swe_bench_pro_candidate_corpus(...)` using the Slice-1 Pro oracle. The produced JSONL is report-only and must include `>=2` oracle-good candidates and `>=1` oracle-bad candidate before it can be called FAR-capable. If live solver attempts or live Pro oracle labeling cannot run, write a blocked artifact and do not emit a fake corpus.

The design follows SWE-bench's benchmark framing: a model is given a codebase and issue and is tasked with generating a patch that resolves the problem. Therefore oracle-bad candidates must be real generated attempts that apply but fail the oracle, not hand-written negative examples. Reference: https://www.swebench.com/SWE-bench/

## PRD Promise Contracts

### P1. Corpus has oracle-good and oracle-bad candidates with provenance.

Public boundary

`build_swe_bench_pro_candidate_corpus(...)` in `supervisor/swe_bench_mergeability.py`, fed real solver attempts plus gold reference attempts and `oracle_runner=run_swe_bench_pro_oracle`.

Chosen seam

An attempt list whose rows contain `instance_id`, `candidate_id`, `model_patch`, `origin`, `producer`, and optional trusted baseline receipt fields. The builder writes `_load_official_predictions(...)`-compatible JSONL rows with stable hashes.

Allowed outcomes

- `pro-predictions.jsonl` has at least two `oracle-good` rows and at least one `oracle-bad` row.
- Every emitted row has `candidate_artifact_hash`, `model_patch_sha256`, `diff_sha256`, `origin`, and `producer`.
- Gold reference rows are marked as dataset reference attempts, not model-generated attempts.
- Generated rows preserve single-agent baseline receipts when present.
- If live generation or oracle labeling is blocked, the artifact says blocked and no FAR-capable corpus is claimed.

Forbidden outcomes

- Synthetic or hand-faked bad patches presented as real solver candidates.
- A gold-only corpus presented as FAR-capable.
- Dropping provenance or receipt fields.
- Treating fixture-only local smoke output as real benchmark evidence.
- Setting any authority flag true.

### P2. `n_bad > 0` makes FAR non-degenerate.

Public boundary

The corpus summary over the predictions JSONL consumed by `_load_official_predictions(...)`.

Chosen seam

`summarize_swe_bench_pro_candidate_corpus(...)` and the summary returned by `build_swe_bench_pro_candidate_corpus(...)`.

Allowed outcomes

- A corpus with at least one oracle-bad row reports `n_bad >= 1`, `false_accept_denominator >= 1`, and `far_degenerate=false`.
- A corpus without oracle-bad rows remains degenerate and cannot be described as a benchmark result.
- Seed readiness is separate from authority: FAR can be non-degenerate while all policy and improvement flags remain false.

Forbidden outcomes

- Reporting `far_degenerate=false` when `n_bad == 0`.
- Interpreting `_rate(..., 0) == 0.0` as measured safety.
- Using FAR readiness to mutate policy or advance an auto-evolve gate.

### P3. Nonresolving counts only when the patch applied and the Pro oracle ran.

Public boundary

The builder binning step that converts oracle outcomes into `oracle-good`, `oracle-bad`, or excluded attempts.

Chosen seam

`_interpret_oracle_outcome(...)` plus the explicit `patch_applied is True` check in `build_swe_bench_pro_candidate_corpus(...)`.

Allowed outcomes

- `pass/pass` plus `patch_applied=true` becomes `oracle-good`.
- `fail/pass`, `pass/fail`, or `fail/fail` plus `patch_applied=true` becomes `oracle-bad`.
- Non-applying, unavailable, malformed, timeout, empty-parser, Docker, and missing-script outcomes are excluded with reasons.

Forbidden outcomes

- Counting non-applying patches as oracle-bad.
- Labeling bad from solver self-report, public tests, reviewer judgment, or any source other than the Pro oracle.
- Counting oracle-unavailable attempts in the FAR denominator.

## User Stories

1. As the benchmark operator, I want the Pro predictions corpus to include real oracle-good and oracle-bad candidates, so that false-accept rate has a denominator.
2. As the benchmark operator, I want gold reference patches included only as good backstops, so that good examples do not mask the absence of generated bad examples.
3. As the benchmark operator, I want generated attempts retained even when the solver rejected them, so that nonresolving patches are available for oracle-bad labeling.
4. As the benchmark operator, I want every candidate row to carry hashes and provenance, so that later all-arms reports can audit where each patch came from.
5. As the benchmark operator, I want non-applying and unavailable attempts excluded, so that infrastructure failures do not become false-accept traps.
6. As the auto-evolve designer, I want all authority flags to remain false, so that a report-only corpus cannot mutate policy.
7. As the next slice owner, I want a precise blocked artifact when live execution is unavailable, so that the next run can resume from the actual missing condition.

## Implementation Decisions

- Reuse the existing candidate-corpus builder and official predictions loader as the public seam.
- Add only focused helpers or tests needed to prove the generated-attempt plus gold-backstop contract.
- Treat the gold proof artifact as evidence that one gold row can be oracle-good, not as permission to synthesize bad rows.
- Treat the single-agent solver local smoke as a contract smoke, not as real model performance.
- Record this slice's real execution status under `docs/dual-agent/pro-corpus-generate-label-20260626/artifacts/`.
- Keep report-only status and all authority flags false.

## Testing Decisions

- First RED test: `tests/test_pro_corpus_generate_label.py::test_corpus_bins_good_and_bad_with_provenance`.
- Second RED test: `tests/test_pro_corpus_generate_label.py::test_n_bad_nonzero_makes_far_real`.
- Third RED test: `tests/test_pro_corpus_generate_label.py::test_nonresolving_only_counts_when_patch_applied`.
- Unit tests fake live solver and Docker below the builder boundary only.
- Real solver and oracle execution are artifact evidence, not unit-test fixtures.

## Out of Scope

- Reviewer-panel work.
- Powered statistics and scaling targets beyond the seed corpus.
- Autonomous benchmark-to-policy bridge work.
- Policy mutation, default-change mutation, or AutoResearch promotion.
- Hand-authoring negative patches to satisfy the FAR denominator.

## Further Notes

The target state for future auto-evolve remains: real benchmark evidence first, then a separate benchmark-to-AutoResearch records bridge. This slice only creates or blocks the real Pro corpus needed before that bridge can be honest.
