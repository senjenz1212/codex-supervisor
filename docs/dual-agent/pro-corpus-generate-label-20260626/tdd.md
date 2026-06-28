# TDD Plan

Task id: `pro-corpus-generate-label-20260626`

## Public Boundary

The first implementation proof is `build_swe_bench_pro_candidate_corpus(...)`, because that is the public seam that converts attempts into official prediction rows. `_load_official_predictions(...)` and `summarize_swe_bench_pro_candidate_corpus(...)` are secondary public checks that prove the written JSONL is consumable and FAR is not degenerate when `n_bad > 0`.

Live solver, Docker, and the Pro oracle are faked below this seam in unit tests. Real generation and labeling are execution artifacts.

## Cycle 1: Corpus Bins Good And Bad With Provenance

RED

Add `tests/test_pro_corpus_generate_label.py::test_corpus_bins_good_and_bad_with_provenance`. It passes two dataset reference attempts and one generated solver attempt through a fake oracle below the builder seam. The expected rows are two `oracle-good` rows and one `oracle-bad` row with provenance, hashes, and receipt fields preserved.

Minimal GREEN

Reuse or minimally extend the corpus builder so it writes all three rows with stable hashes, loader-compatible JSONL, and false authority flags.

## Cycle 2: `n_bad > 0` Makes FAR Real

RED

Add `tests/test_pro_corpus_generate_label.py::test_n_bad_nonzero_makes_far_real`. It summarizes a fixture corpus with at least one oracle-bad row and asserts `n_bad >= 1`, `false_accept_denominator >= 1`, and `far_degenerate is False`.

Minimal GREEN

Reuse or minimally extend the corpus summary helper. Do not change `_rate(...)` and do not claim powered adequacy.

## Cycle 3: Nonresolving Only Counts When Patch Applied

RED

Add `tests/test_pro_corpus_generate_label.py::test_nonresolving_only_counts_when_patch_applied`. It supplies two failing oracle outcomes: one with `patch_applied=true`, one with `patch_applied=false`. Only the applying one may become `oracle-bad`.

Minimal GREEN

Keep or add the explicit `patch_applied is True` requirement before writing oracle-bad rows.

## Cycle 4: Gold-Only Degeneracy Stays Visible

RED

Add `tests/test_pro_corpus_generate_label.py::test_gold_only_corpus_is_completed_but_far_degenerate`. It writes only oracle-good gold rows and asserts `status="completed"` means rows were written, while `n_bad == 0`, `false_accept_denominator == 0`, `far_degenerate is True`, and authority flags remain false.

Minimal GREEN

Keep summary semantics explicit and avoid any benchmark-ready alias that ignores `far_degenerate`.

## Cycle 5: Execution Artifact

RED

No unit test. Before the execution attempt, this packet lacks current evidence showing a real solver k>1 run and Pro oracle labels.

Minimal GREEN

Write `artifacts/corpus-generate-label-execution-status.json`. If the live runner or host cannot execute the real path, record exact blockers and do not write a FAR-capable corpus.

## Refactor Check

- Keep the builder as the deep module seam.
- Avoid a second JSONL format.
- Avoid any helper that invents oracle labels.
- Keep policy and auto-evolve bridge work out of scope.

## Accepted Ledger Record

```jsonl
{"stage":"tdd_review","task_id":"pro-corpus-generate-label-20260626","status":"accepted","first_red":"tests/test_pro_corpus_generate_label.py::test_corpus_bins_good_and_bad_with_provenance","evidence":["docs/dual-agent/pro-corpus-generate-label-20260626/tdd.md","docs/dual-agent/pro-corpus-generate-label-20260626/grill-findings-tdd.md","docs/dual-agent/pro-corpus-generate-label-20260626/translation-audit.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
