# Tri-Agent Findings

Task id: `pro-corpus-generate-label-20260626`

## Validator A - Oracle-grounded binning

Verdict: revise.

Evidence:

- `build_swe_bench_pro_candidate_corpus(...)` calls `oracle_runner(attempt)`, excludes unavailable outcomes, requires `patch_applied is True`, and then maps `pass/pass` to `oracle-good` and nonresolving outcomes to `oracle-bad` (`supervisor/swe_bench_mergeability.py:4470`, `:4495`).
- The Pro oracle adapter fail-closes missing/malformed patch-apply evidence (`supervisor/swe_bench_official_oracle.py:543`, `:891`).
- Existing tests cover `patch_applied=false` exclusion (`tests/test_swe_bench_pro_candidate_corpus.py:123`).
- Current candidate-corpus execution evidence is still unavailable, with no real `pro-predictions.jsonl`.

Fold-back:

- Keep the builder path.
- Add this slice's own tests around generated plus gold attempts.
- Record live execution as blocked if no real solver attempts are available.

## Validator B - Provenance and hashes

Verdict: revise.

Evidence:

- Solver attempts compute and preserve patch hashes (`supervisor/swe_bench_solver.py:206`).
- The builder recomputes hash fields and rejects duplicate generated hashes (`supervisor/swe_bench_mergeability.py:4514`).
- The builder preserves optional baseline receipt keys (`supervisor/swe_bench_mergeability.py:4538`).
- Provenance is preserved but not enforced because empty `origin`/`producer` become `{}` (`supervisor/swe_bench_mergeability.py:4311`, `:4529`).

Fold-back:

- Add validation that corpus rows require non-empty `origin` and `producer`.
- Require gold backstops to use explicit dataset-reference provenance.
- Do not fabricate baseline receipts for gold rows.

## Validator C - FAR honesty

Verdict: revise.

Evidence:

- Corpus summaries correctly compute `n_bad`, `false_accept_denominator`, and `far_degenerate` (`supervisor/swe_bench_mergeability.py:4452`).
- The builder returns `status="completed"` when any rows are written, so a gold-only corpus can be completed but still FAR-degenerate (`supervisor/swe_bench_mergeability.py:4546`).
- Mergeability denominator math uses oracle-negative rows and `_rate(..., 0)` returns `0.0` (`supervisor/mergeability_bench.py:4213`, `:4243`, `:5916`).
- Authority flags remain false in the corpus and bridge paths (`supervisor/swe_bench_mergeability.py:4554`).

Fold-back:

- Add a gold-only regression asserting `n_bad == 0`, denominator `0`, `far_degenerate is True`, and no authority claim.
- Keep `status="completed"` as a write-status, but ensure reports/tests never equate it with FAR readiness.

## Final Fold-back Decision

Accepted after revisions. Implementation may proceed only with:

- provenance enforcement,
- gold-only degeneracy coverage,
- non-applying exclusion coverage,
- blocked execution artifact if real solver/oracle generation cannot run.
