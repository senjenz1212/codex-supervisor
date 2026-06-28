# Pro Single-Agent Solver PRD

Task id: `pro-single-agent-solver-20260626`

Depends on: `pro-oracle-gold-proof-20260626` for oracle proof, but this slice starts in parallel with downstream oracle-labeling work.

## Problem Statement

SWE-bench Pro corpus generation still lacks the real single-agent attempt producer. The CLI can shell out to generator commands through `_command_generator`, but `supervisor/swe_bench_solver.py` refuses live execution and the corpus builder can only label attempts it is handed. Without a generator that reads the public packet, runs one agent more than once, captures real diffs, and emits trusted baseline receipts, the benchmark cannot produce oracle-bad candidates from non-resolving attempts.

## Solution

Build a safe-by-default generator program at the existing solver seam. It reads `SWEBENCH_MERGEABILITY_GENERATOR_INPUT`, runs one configured agent `k > 1` times against the public worktree, captures each resulting diff via `capture_model_patch`, and writes `SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT` with an attempts list. Each attempt carries a `model_patch`, provenance, and a trusted `single_agent_baseline_decision` receipt whose `candidate_artifact_hash` matches `sha256(model_patch)`. Unit tests fake the agent runner below the generator boundary; the real model run is an execution artifact, not a pytest dependency.

## PRD Promise Contracts

### P1. Generator emits real per-attempt patches via the CLI seam.

Public boundary

The program invoked by `_command_generator` in `supervisor/swe_bench_mergeability_cli.py` reading `SWEBENCH_MERGEABILITY_GENERATOR_INPUT` and writing `SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT`.

Chosen seam

`capture_model_patch(...)` in `supervisor/swe_bench_solver.py` after each single-agent attempt mutates the public worktree.

Allowed outcomes

- With `--allow-live` and `--max-budget-usd > 0`, the generator invokes one configured agent runner `k > 1` times.
- The output contains `attempts` with one entry per attempt, and every successful attempt has a non-empty `model_patch`.
- The top-level `model_patch` remains available for existing single-candidate consumers and mirrors the first attempt.
- Tests fake only the runner below the generator boundary.

Forbidden outcomes

- The production path reaches the existing refusal stub after live consent is supplied.
- Fabricated, static, or empty diffs are reported as attempts.
- Hidden oracle fields are read, copied, logged, or passed to the runner.

### P2. Trusted baseline receipt.

Public boundary

`single_agent_baseline_decision` emitted by the generator and validated by `_resolve_powered_baseline_decision(...)` in `supervisor/mergeability_bench.py`.

Chosen seam

The generator computes `candidate_artifact_hash=sha256(model_patch)` and emits `decision_source="single_agent_candidate_generation"` with replay evidence fields.

Allowed outcomes

- Each attempt includes `{candidate_id, accept, decision_source, candidate_artifact_hash, prompt_sha256, producer}`.
- `producer` includes `model`, `provider`, and `runner_label`.
- Hash-matched receipts resolve available; hash-mismatched receipts resolve unavailable.

Forbidden outcomes

- Legacy booleans count as trusted receipts.
- Hash mismatches, missing producer fields, missing prompt hashes, or untrusted decision sources resolve available.
- The generator copies oracle labels into the baseline receipt.

### P3. Non-resolving capture for oracle-bad.

Public boundary

The attempts list consumed by `build_swe_bench_pro_candidate_corpus(...)` in `supervisor/swe_bench_mergeability.py`.

Chosen seam

The generator retains every applying attempt with a non-empty diff and leaves oracle-good/bad labeling to the oracle-backed corpus builder.

Allowed outcomes

- `k > 1` attempts are retained even when the generator's own baseline decision rejects them.
- Non-resolving diffs are preserved for later oracle-bad labeling.
- The dataset reference patch can be fed as one attempt externally as the oracle-good backstop; no separate gold generator is introduced.

Forbidden outcomes

- Discarding non-resolving diffs because they were not accepted by the single agent.
- Reusing an eval-only resolved filter that keeps only successful attempts.
- Advancing any metric, improvement, default-change, policy, or gate authority flag.

## Out Of Scope

- Running or modifying the Pro oracle.
- Reviewer panels and powered statistics.
- Autonomous benchmark-to-policy bridge or policy mutation.
- Claiming that the real execution artifact proves benchmark improvement.
