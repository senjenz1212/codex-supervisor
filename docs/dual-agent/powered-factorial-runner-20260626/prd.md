# Powered Factorial Runner PRD

Task id: `powered-factorial-runner-20260626`

Depends on: `pro-corpus-generate-label-20260626`

## Problem Statement

The powered factorial evaluator already exists, but it is not reachable from a production SWE-bench Pro workflow. Operators can run all-arms diagnostics, yet that path is explicitly report-only and keeps all authority flags false. The powered verdict lives in `run_powered_factorial_mergeability_evaluation(...)`, which currently has no production runner over the Pro-labeled corpus. That leaves the benchmark stuck between useful diagnostics and no executable powered report.

## Solution

Add an explicit powered factorial runner and CLI path that consumes the Pro-labeled predictions JSONL, adapts those rows into the powered factorial evaluator, threads operator-configurable power thresholds, and emits an `evidence_conversion_power_contract`. The all-arms diagnostic remains unchanged and report-only. The powered report is still report-only for policy mutation, but it can say whether the evidence is statistically qualified for later operator-reviewed conversion.

The paired comparison uses the existing McNemar/discordant-pair implementation. NIST describes McNemar as a test for paired binary outcomes that focuses on discordant cells; this slice makes that paired gate explicit rather than relying on raw good/bad denominators alone.

## PRD Promise Contracts

### P1. Powered factorial has a production runner/CLI consuming the Pro corpus.

Public boundary

`supervisor.swe_bench_mergeability_cli` exposes an explicit powered factorial mode that reads `--predictions`, writes a powered report under `--output-dir`, and calls `run_powered_factorial_mergeability_evaluation(...)`.

Chosen seam

The runner adapts `_load_official_predictions(...)` rows into the factorial evaluator's `arm_decisions`, `reviewer_panel_results`, precomputed oracle outcomes, public decisions, and candidate artifact hashes.

Allowed

- The runner consumes the Pro predictions JSONL and records that path in the report.
- The runner calls `run_powered_factorial_mergeability_evaluation(...)` with `powered_thresholds`.
- Baseline decisions use trusted produced baseline receipts and hash-match against the Pro candidate artifact hash.
- Full-stack and other arm decisions come from explicit corpus fields or fail closed.
- The all-arms diagnostic remains separate and unchanged.

Forbidden

- Leaving the powered evaluator reachable only from tests.
- Running silently on `tests/fixtures/mergeability_bench` or another local fixture corpus.
- Defaulting missing Pro arm decisions into a qualified powered result.
- Mutating policy, changing defaults, or building the autonomous benchmark-to-policy bridge.

### P2. Power contract is emitted and threshold-controlled.

Public boundary

The powered report contains `evidence_conversion_power_contract`, and the CLI exposes `--min-good`, `--min-bad`, `--min-discordant`, and `--alpha`.

Chosen seam

The runner threads thresholds into `_factorial_sample_size_sufficiency(...)` and `_paired_power_sufficiency(...)`, then derives the contract from `sample_size_sufficiency`, `paired_power`, and the required `full_supervisor_stack` comparison.

Allowed

- `status="qualified"` only when `n_good >= min_good`, `n_bad >= min_bad`, discordant pairs meet `min_discordant`, and the McNemar p-value is `<= alpha`.
- Underpowered reports explain which threshold failed.
- The report preserves `improvement_claim_allowed=False`, `default_change_allowed=False`, `policy_mutated=False`, and `gate_advanced=False`.

Forbidden

- A CLI that cannot set the power floors.
- `status="qualified"` without the discordant/McNemar gate passing.
- Treating a qualified contract as permission for automatic policy mutation.

## User Stories

1. As a benchmark operator, I want one CLI command for the powered Pro corpus path, so that I do not have to stitch test-only functions together.
2. As a benchmark operator, I want the runner to refuse unlabeled or incomplete corpus rows, so that a powered report cannot hide missing evidence.
3. As a benchmark operator, I want threshold flags, so that seed and scale runs can be explicit about statistical floors.
4. As an auto-evolve maintainer, I want an evidence conversion power contract, so that later bridge work can distinguish powered evidence from report-only diagnostics.
5. As an auto-evolve maintainer, I want all mutation flags to remain false, so that powered benchmark evidence does not become an autonomous bridge by accident.

## Implementation Decisions

- Add a deep runner interface in `supervisor/swe_bench_mergeability.py` rather than overloading the all-arms diagnostic.
- Reuse `_load_official_predictions(...)` as the Pro corpus loader and extend it only where needed to preserve explicit arm decisions.
- Extend `run_powered_factorial_mergeability_evaluation(...)` with opt-in precomputed Pro evidence instead of running the local grading corpus.
- Generate a transient mergeability manifest from the Pro corpus only to satisfy the evaluator's candidate-pool interface; the report records the original Pro corpus path and hashes.
- Fail closed when required arm decisions, oracle labels, or trusted baseline receipts are missing.

## Testing Decisions

- First RED test: `tests/test_powered_factorial_runner.py::test_cli_exposes_power_thresholds`.
- Public-boundary tests drive the CLI and the Pro-powered runner, not private helper functions.
- Unit tests fake live infrastructure below the Pro corpus boundary by writing fixture JSONL rows.
- Existing paired-power tests continue to cover McNemar and Wilson math inside the evaluator.

## Out of Scope

- Autonomous benchmark-to-policy bridge work.
- Changing the all-arms diagnostic authority flags.
- Running the live Pro solver or Pro Docker oracle.
- Reviewer-panel construction.
- Powering math beyond the existing McNemar and Wilson implementation.

## Further Notes

This slice makes powered evidence reachable. It does not claim that a current corpus is large enough, nor that any policy can mutate from the report without a separate, operator-reviewed bridge.
