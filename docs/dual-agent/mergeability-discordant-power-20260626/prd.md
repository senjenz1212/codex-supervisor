# Mergeability Discordant Power PRD

Task id: `mergeability-discordant-power-20260626`

## Problem Statement

The powered factorial mergeability report can currently call the evidence "sufficient" when raw oracle buckets meet `n_bad >= 30` and `n_good >= 30`, even if the paired baseline-vs-supervisor decisions are all concordant. That lets a report with no paired information pass the power gate and makes `metric_applyable` look stronger than the data support.

The rate reporting also risks overstating certainty. A bare zero FAR, TAR, or FRR without an interval can be read as proof of zero risk. Zero-event arms need an honest upper bound, and all rate displays need consistent interval fields.

## Solution

Add a paired/discordant power block to the powered factorial report. The block consumes `_paired_discordant_counts`, runs an exact binomial McNemar test for sparse discordance and a continuity-corrected chi-square McNemar test once the discordant-pair threshold is reached, and gates `metric_applyable` through that paired result.

Extend interval reporting so every FAR, TAR, and FRR surface carries an interval. Preserve Wilson score intervals for ordinary rates and use a rule-of-three upper bound for zero-event arms.

## User Stories

1. As the benchmark owner, I want all-concordant rows reported as underpowered, so that raw sample counts cannot masquerade as paired evidence.
2. As the auto-evolution reviewer, I want `metric_applyable` to require paired discordant evidence, so that report flags cannot advance on denominator sufficiency alone.
3. As a report reader, I want sparse discordance to use an exact binomial test, so that small samples do not inherit asymptotic confidence.
4. As a report reader, I want larger discordance to use a continuity-corrected McNemar chi-square test, so that the paired test matches the usual large-sample approximation.
5. As a report reader, I want every FAR/TAR/FRR rate to carry interval fields, so that point estimates are not mistaken for certainty.
6. As a safety reviewer, I want zero-event rates to expose a rule-of-three upper bound, so that `0.0` still communicates residual risk.
7. As the policy-evolution owner, I want all authority flags to remain false, so that this report-hardening change does not build or imply the autonomous benchmark-to-policy bridge.

## PRD Promise Contracts

### P1. Powering is paired/discordant, not raw counts.

Public boundary

The powered factorial report's `paired_power` block and top-level `metric_applyable` result.

Chosen seam

`_paired_power_sufficiency` consumes the output of `_paired_discordant_counts`.

Allowed outcomes

`status="sufficient"` is possible only when the required comparison has at least the configured discordant-pair threshold and the paired McNemar result passes at the configured alpha. Sparse nonzero discordance reports `method="exact_binomial_two_sided"` and remains underpowered. Threshold-reaching discordance reports `method="mcnemar_chi_square_continuity_corrected"`. Raw `n_good` and `n_bad` can remain visible but cannot satisfy the paired gate alone.

Forbidden outcomes

Raw `n_good`/`n_bad` alone satisfying powered. Declaring sufficient with all-concordant rows. Flipping `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, or `gate_advanced` to true.

### P2. Every FAR/TAR/FRR rate carries an honest interval.

Public boundary

The report `far_tar_frr` block and powered factorial arm summaries.

Chosen seam

`_wilson_interval` applied per rate, with the zero-event case exposing the rule-of-three upper bound.

Allowed outcomes

Each rate exposes `rate`, `ci_low`, and `ci_high` fields or an equivalent confidence interval object with lower and upper bounds. FAR `0/n` reports a lower bound of `0.0` and an upper bound near `3/n`.

Forbidden outcomes

A bare `0.0` read as certainty. Wald intervals. Missing FRR interval fields. Changing oracle adapters, candidate generation, baseline production, reviewer panel behavior, or the autonomous benchmark-to-policy bridge.

## Implementation Decisions

- Reuse the powered factorial report as the public module interface; do not introduce a new runner.
- Keep raw sample size reporting as descriptive denominator evidence, but stop treating it as the sole powered gate. Promotion guardrails must distinguish raw sample-size sufficiency from paired-power sufficiency.
- Add a compact `paired_power` report block rather than overloading `paired_discordant_counts`.
- Treat `full_supervisor_stack` vs `single_agent_baseline` as the required paired comparison for `metric_applyable`.
- Keep `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` false.
- Do not build the autonomous benchmark-to-AutoResearch promotion bridge.

## Testing Decisions

- The first RED test starts at `run_powered_factorial_mergeability_evaluation` and asserts all-concordant rows are underpowered at the report boundary.
- Tests use fixture candidate decisions below the public boundary. They do not mock the paired-power decision above the boundary.
- Sparse-discordance testing asserts exact-binomial method selection, underpowered status, and `metric_applyable=False` through the report block.
- Threshold-reaching discordance testing asserts continuity-corrected McNemar chi-square method selection through the report block.
- Interval tests assert public report fields for rule-of-three, Wilson/rate coverage, FRR projection, and non-Wald provenance.

## Out of Scope

- Oracle adapter changes.
- Candidate generation changes.
- Baseline production changes.
- Reviewer panel changes.
- SWE-bench live, Docker, or network calls.
- Autonomous benchmark-to-policy bridge, policy proposal creation, or automatic policy mutation.
