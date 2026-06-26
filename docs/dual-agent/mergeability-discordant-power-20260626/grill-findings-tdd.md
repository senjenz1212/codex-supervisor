# TDD Grill Findings

Gate: `tdd_review`

Decision: accepted after revisions below.

## Finding 1: The first RED must not test the helper directly.

Severity: blocking before revision.

Evidence:

- The requested public boundary is the report's `paired_power` block and `metric_applyable`.
- `_paired_power_sufficiency` is the chosen seam, not the first test boundary.

Resolution:

Cycle 1 starts at `run_powered_factorial_mergeability_evaluation` and asserts public report behavior.

Status: resolved.

## Finding 2: Sparse nonzero discordance must block applyability.

Severity: blocking before revision.

Evidence:

- Agent B found Cycle 2 only pinned exact-binomial method selection, not paired-power status or `metric_applyable` outcome.
- `supervisor/mergeability_bench.py:2017` previously wired powered status from raw sample-size sufficiency.

Resolution:

Cycle 2 now requires raw sample size to be sufficient while sparse nonzero discordance remains underpowered and keeps `metric_applyable` false.

Status: resolved.

## Finding 3: Threshold-reaching discordance needs an explicit chi-square RED.

Severity: blocking before revision.

Evidence:

- Agent A found the first TDD plan named exact-binomial sparse testing but no continuity-corrected McNemar threshold test.

Resolution:

Cycle 3 now adds `test_continuity_corrected_mcnemar_used_at_threshold`.

Status: resolved.

## Finding 4: Interval parity must include FRR, zero-event TAR/FRR, and non-Wald provenance.

Severity: blocking before revision.

Evidence:

- Agent C found the public `far_tar_frr` projection could omit FRR intervals.
- The original zero-event test named FAR only.

Resolution:

Cycles 4 and 5 now require zero-event FAR/TAR/FRR rule-of-three behavior, FRR interval projection in `far_tar_frr`, and interval method provenance.

Status: resolved.

## Finding 5: TDD must not widen scope into policy promotion.

Severity: medium.

Evidence:

- Prior supervisor memory identifies benchmark-to-AutoResearch promotion as a separate bridge.
- This task says not to build the autonomous benchmark-to-policy bridge.

Resolution:

The TDD plan keeps authority flags false and runs only code-only fixture tests.

Status: resolved.
