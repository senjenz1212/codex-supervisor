# Powered Real Benchmark Run PRD

Task id: `powered-real-benchmark-run-20260626`

Depends on: Slices 1-6 (`pro-oracle-gold-proof`, `pro-single-agent-solver`, `pro-runscripts-coverage`, `pro-corpus-generate-label`, `panel-verified-crossfamily-robust`, `powered-factorial-runner`)

## Problem Statement

The powered factorial runner now exists, but a "real benchmark" claim still needs a scaled, oracle-labeled SWE-bench Pro corpus and a verified panel run. The repository currently has blocked corpus-generation evidence, not the 100-150 candidate rows expected to reach `>=30` oracle-good, `>=30` oracle-bad, and `>=25` discordant paired comparisons. Without a hard Definition-of-Done checker, a future run could be mistaken for the real benchmark while still underpowered or mislabeled as maintainer mergeability.

The research reason for the proxy label is concrete: METR reports that "roughly half" of test-passing SWE-bench Verified PRs would not be merged and that the automated grader was "about 24.2 percentage points" higher than maintainer merge decisions. Direct reference: https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/

## Solution

Add a report-only DoD checker for the scaled powered run. The checker accepts two artifacts: the powered factorial report from Slice 6 and the separate all-arms diagnostic status from the Pro benchmark path. It passes only when sample size, paired McNemar power, FAR/TAR/FRR confidence intervals, panel marginal evidence, source provenance, and report-only labels all hold simultaneously. The live scaled run is not unit-tested; it remains execution evidence. In this environment, no scaled artifact exists, so the slice must also commit an honest blocked execution-status artifact.

NIST describes McNemar for "two paired variables" with binary outcomes, so this checker treats discordant paired comparisons as a required gate rather than relying only on raw good/bad counts. Direct reference: https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm

## PRD Promise Contracts

### P1. The artifact is powered and complete.

Public boundary

Powered factorial report plus separate all-arms diagnostic status.

Chosen seam

`assert_powered_real_benchmark_definition_of_done(powered_report=..., all_arms_diagnostic_report=...)`

Allowed

- `_factorial_sample_size_sufficiency` reports `status="sufficient"` with `n_good >= 30` and `n_bad >= 30`.
- `_paired_power_sufficiency` reports `status="sufficient"` for `full_supervisor_stack`, with `discordant_pair_count >= 25`, `mcnemar_test_passed=true`, and `p_value <= alpha`.
- `far_tar_frr` is non-null and carries confidence-interval blocks for the all-arms diagnostic arms.
- Panel marginal evidence is present.
- Source prediction provenance is present and is not a fixture, synthetic, smoke, or gold-only path.

Forbidden

- Declaring a real benchmark while underpowered.
- Accepting `far_tar_frr=null`.
- Treating gold-only or fixture rows as a scaled real corpus.
- Treating raw good/bad counts as powered when the paired discordant/McNemar gate fails.

### P2. The artifact is honest and report-only.

Public boundary

Artifact labels and authority flags in the powered report and all-arms diagnostic status.

Chosen seam

The same Definition-of-Done checker verifies `benchmark_oracle`, report-only blocks, and authority flags across both artifacts.

Allowed

- `benchmark_oracle.kind="swe_bench_held_out_test_pass_proxy"`.
- `maintainer_mergeability_claim_allowed=false`.
- `no_maintainer_mergeability_claim=true`.
- All authority flags stay false: `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced`.
- The powered report may expose `powered_metric_applyable=true` as diagnostic evidence, while `metric_applyable=false` remains the authority flag.

Forbidden

- Calling the result maintainer mergeability.
- Any authority flag true.
- Any policy/default mutation permission in the report, recommendation, or evidence-conversion contract.

## User Stories

1. As a benchmark operator, I want a single DoD checker for the powered report and all-arms status, so that a scaled run cannot be accepted by prose alone.
2. As a benchmark operator, I want raw sample-size and paired-power gates checked together, so that `30/30` counts do not mask zero or weak discordant evidence.
3. As a benchmark operator, I want FAR/TAR/FRR confidence intervals required, so that the benchmark artifact carries uncertainty, not just point estimates.
4. As a benchmark operator, I want source provenance checked, so that fixture, smoke, synthetic, or gold-only paths cannot be mislabeled as real scale.
5. As an auto-evolve maintainer, I want all authority flags false, so that a benchmark run never mutates policy or defaults.
6. As an auto-evolve maintainer, I want the oracle labeled as a held-out test-pass proxy, so that the artifact does not overclaim human maintainer mergeability.
7. As a future runner, I want a blocked artifact when scale evidence is absent, so that the next run starts from truthful blockers.

## Implementation Decisions

- Add a small checker module rather than extending the live runner. The live run is execution evidence; the new code only validates output artifacts.
- Keep the checker's public interface deep: one function returns or raises with reason codes; helper checks stay private.
- Treat the powered report and all-arms diagnostic as separate inputs because the powered runner intentionally does not own the all-arms oracle-labeling block.
- Require the paired `full_supervisor_stack` comparison because the central question is supervisor stack vs single-agent baseline, not independent raw rates.
- Preserve the report-only split: `powered_metric_applyable` can be diagnostic, but authority flags remain false.

## Testing Decisions

- First RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_powered`, failing on the missing checker module.
- Keep tests at the public checker seam with report-shaped fixture mappings.
- Do not mock the live runner, Docker, solver, or model path above this seam.
- Add a fail-closed regression proving underpowered sample size is rejected.

## Out of Scope

- Running the scaled benchmark on this macOS host.
- Building the autonomous benchmark-to-policy bridge.
- Mutating policies, defaults, or gates.
- Reworking powered-factorial math.
- Generating the Pro solver corpus.

## Further Notes

Current execution status is blocked, not done: the repository has no checked-in powered factorial report and no real scaled Pro predictions corpus. The committed artifact for this slice records that honestly.
