# Tri-Agent Findings

Task id: `swe-bench-pro-candidate-corpus-20260626`

## Validator A: Loader Contract

Verdict: REVISE

Evidence:

- `_load_official_predictions(...)` currently preserves only `candidate_id`, `model_patch`, and optional baseline receipt mappings, so corpus provenance fields would be dropped before replay.
- The official replay candidate construction forwards only the patch, hidden oracle context, and one baseline receipt alias; candidate `origin`, `producer`, `candidate_artifact_hash`, `model_patch_sha256`, `diff_sha256`, and `oracle_label` do not survive into generated replay artifacts.
- P1 in `prd.md` requires these fields as public corpus contract evidence.

Required fold-back:

- Normalize prediction JSONL rows with stable `candidate_artifact_hash`, `model_patch_sha256`, `diff_sha256`, `origin`, `producer`, and optional `oracle_label`.
- Reject duplicate candidate hashes and mismatched supplied hashes.
- Forward corpus metadata through replay candidates and report rows.

## Validator B: Oracle-Grounded Binning

Verdict: REVISE

Evidence:

- The plan correctly defines `oracle-bad` as patch-applies-but-tests-fail, but the Pro oracle adapter can run `git apply` without an explicit patch-apply receipt.
- A failed patch apply could be indistinguishable from a test failure at the downstream binning boundary.
- `_interpret_oracle_outcome(...)` and diagnostic counting need a guard so non-applying attempts cannot contribute to `n_bad`.

Required fold-back:

- Make the Pro oracle adapter emit `patch_applied` evidence and mark patch-apply failure unavailable.
- Require explicit `patch_applied == true` before candidate-corpus builder rows can be labeled `oracle-good` or `oracle-bad`.
- Add a non-applying regression test.

## Validator C: FAR Honesty

Verdict: PASS, plan gate only

Evidence:

- `n_bad` is the oracle-negative denominator and `_rate(...)` returns `0.0` when that denominator is empty, so a gold-only corpus is not FAR-capable.
- The packet correctly forbids blocked execution from masquerading as `pro-predictions.jsonl`.
- `artifacts/` still needs real execution evidence, either a valid corpus with `n_bad > 0` or an honest blocked artifact.

Required fold-back:

- Add a corpus summary that reports `n_bad` and marks FAR degenerate when no oracle-bad candidates exist.
- Emit a blocked execution artifact if the local solver/Docker/oracle environment cannot produce real generated candidates.

## Final Fold-Back Decision

Status: accepted after revisions.

Implementation must repair the loader/provenance path, the patch-applied oracle receipt, and the corpus builder binning seam before producing any execution artifact. If real solver/oracle execution remains unavailable on this host, the only acceptable packet artifact is blocked/unavailable with every authority flag false.
