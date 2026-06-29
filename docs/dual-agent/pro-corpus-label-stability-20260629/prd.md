# Pro Corpus Label Stability PRD

Task id: `pro-corpus-label-stability-20260629`

## Problem Statement

The SWE-bench Pro corpus builder labels each candidate once through the Pro oracle. A one-shot held-out-test label can be wrong when the test path flakes, times out, or produces inconsistent outcomes. If a flaky candidate is kept, downstream FAR/TAR/FRR can be corrupted while still looking precise.

## Solution

Add a report-only label-stability wrapper that re-runs the Pro oracle `N` times per already-labeled prediction row, keeps only candidates whose repeated `(fail_to_pass_status, pass_to_pass_status)` pairs agree, and records every dropped candidate plus the flake rate. The wrapper is importable for unit tests with an injected fake oracle, and the CLI refuses live Docker oracle work unless `--allow-live` is supplied.

## PRD Promise Contracts

### P1. Stable candidates are kept without relabeling.

Public boundary

`repeat_oracle_labels(...)` plus `filter_stable_corpus(...)` in `scripts/swebench_pro_label_stability.py`.

Chosen seam

Predictions JSONL rows plus curated dataset records keyed by `instance_id`; the wrapper reuses the batch driver's Pro oracle context construction before invoking the oracle runner.

Allowed outcomes

- A candidate with `repeats` completed runs that all agree is kept.
- The kept JSONL row preserves the original `oracle_label`, `candidate_artifact_hash`, `model_patch_sha256`, and `diff_sha256`.
- The repeated oracle statuses are recorded in a report-only flake report.

Forbidden outcomes

- Recomputing or changing the original `oracle_label`.
- Mutating stable prediction rows while filtering.
- Deriving Pro oracle context differently from the corpus builder.

### P2. Flaky or unavailable candidates are dropped honestly.

Public boundary

`classify_stability(...)` and `filter_stable_corpus(...)`.

Chosen seam

The stability classification over repeated oracle run summaries: `STABLE`, `UNSTABLE`, or `UNAVAILABLE`.

Allowed outcomes

- Completed runs with disagreeing status pairs are dropped as `unstable_label`.
- Any unavailable repeat is dropped fail-closed as `oracle_unavailable:<reason>`.
- `flake_rate` is `unstable / total_evaluated`.
- Empty input and all-dropped output fail closed instead of writing an empty corpus as success.

Forbidden outcomes

- Counting unavailable oracle runs as flakes.
- Silently dropping candidates.
- Writing an empty stable corpus after live budget is spent.

### P3. Live oracle re-runs are explicit and report-only.

Public boundary

The `scripts/swebench_pro_label_stability.py` CLI.

Chosen seam

CLI flags `--records`, `--predictions`, `--swe-bench-pro-scripts-dir`, `--repeats`, `--output`, `--report`, `--oracle-timeout-s`, and `--allow-live`.

Allowed outcomes

- Without `--allow-live`, the CLI exits non-zero before any oracle call.
- With `--allow-live`, the CLI writes stable JSONL plus a flake report.
- The flake report keeps all authority flags false and uses env-var names only for live configuration.

Forbidden outcomes

- Unit tests calling Docker, live Pro oracle, solver, Telegram, Anthropic, or OpenAI.
- Writing token values into stable corpus or report artifacts.
- Setting any benchmark, policy, or auto-evolve authority flag true.

## User Stories

1. As the benchmark operator, I want flaky Pro oracle labels filtered out, so that FAR/TAR/FRR are not built on one-shot label noise.
2. As the benchmark operator, I want dropped candidates and reasons recorded, so that exclusions are auditable.
3. As the benchmark operator, I want stable rows preserved byte-for-byte in meaning, so that label-stability never becomes a relabeling step.
4. As the VM operator, I want live re-runs guarded by `--allow-live`, so that local tests and dry commands cannot spend Docker budget accidentally.
5. As the auto-evolve reviewer, I want all authority flags false, so that this gate filters benchmark evidence but never promotes policy.

## Implementation Decisions

- Build one deep wrapper module with a small importable interface.
- Reuse `scripts.swebench_pro_batch_driver._augment_attempt_with_oracle_context` so Pro context fields stay aligned with `build_and_label_corpus`.
- Treat patch-apply failures and malformed oracle returns as unavailable.
- Keep the report free of patch text and env values; it stores ids, hashes, repeated statuses, reasons, counts, and false authority flags.
- Live execution remains an operator action on the VM, not a unit-test behavior.

## Testing Decisions

- Tests exercise the public importable functions and CLI with an injected fake oracle.
- The first RED test proves a stable `(pass, pass)` candidate is kept with original label and Pro oracle context fields.
- Subsequent tests cover unstable disagreement, unavailable fail-closed behavior, flake-rate math, no mutation, empty/all-dropped failure, live opt-in refusal, and absence of secret env values in outputs.

## Out of Scope

- Changing the Pro oracle.
- Changing solver, batch driver core, runner, panel, or powered factorial logic.
- Live VM execution of the stability wrapper.
- Autonomous benchmark-to-policy promotion.
