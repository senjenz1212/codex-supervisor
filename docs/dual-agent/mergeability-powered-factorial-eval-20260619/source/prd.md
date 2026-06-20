## Problem Statement

AutoResearch now has oracle-isolated mergeability calibration, a public-check arm, a full reviewer-panel arm, and live candidate generation, but it still cannot estimate the final supervisor-specific parameter. Operators need a powered, report-only factorial evaluation that separates generation quality, multi-agent structure, reviewer diversity, runtime evidence, and the complete supervisor stack. Without that decomposition, a headline false-accept number can hide unequal candidate pools, unmatched true-accept rates, reviewer unavailability, oracle ceiling leakage, or gaming flags.

## Solution

Add a powered factorial mergeability report that evaluates the same candidate pool across independently labeled arms: single-agent baseline, same-model multi-agent, hetero or multi-reviewer structure, runtime-evidence floor only, full supervisor stack, and oracle ceiling. The report will compute false accept, true accept, false reject, matched true-accept status, paired discordant counts, confidence intervals, sample-size sufficiency, leave-one-reviewer-out effects, reviewer correlation indicators, replay artifact references, and observational trend rows. All outputs remain report-only unless a separate operator-approved policy-evolution proposal later consumes powered evidence.

## User Stories

1. As a supervisor operator, I want each factorial arm clearly labeled, so that I can tell which part of the supervisor stack produced a measured effect.
2. As an evaluation owner, I want all compared arms to use the same candidate pool, so that paired differences are attributable to evaluation structure rather than changed cases.
3. As a policy reviewer, I want false accept measured at matched true accept, so that stricter gates are not mistaken for better supervisors when they merely reject more valid solutions.
4. As a reviewer-panel maintainer, I want leave-one-reviewer-out analysis, so that one reviewer’s marginal contribution and correlation with the panel are visible before any promotion decision.
5. As an AutoResearch maintainer, I want gaming flags and reviewer unavailability to block applyable claims, so that hollow or incomplete experiments cannot feed policy mutation.
6. As a benchmark maintainer, I want oracle ceiling labeled as a ceiling, so that answer-key performance cannot be reported as supervisor improvement.
7. As a future investigator, I want replayable artifacts and trend rows, so that a reported metric can be reconstructed without recomputing unrelated ledger history.

## PRD Promise Contracts

P1. The factorial report will include exactly the expected arms, with independent labels and decision sources for single-agent baseline, same-model multi-agent, hetero multi-reviewer, runtime-evidence floor, full supervisor stack, and oracle ceiling.
P2. Every compared non-oracle arm will be evaluated over the same candidate identifiers, task identifiers, oracle labels, and split metadata, and the report will reject comparisons when candidate pools diverge.
P3. Matched true-accept analysis will report FAR, TAR, FRR, paired discordant counts, confidence intervals, and a status that refuses unmatched comparisons instead of silently comparing stricter or looser arms.
P4. The oracle ceiling will remain marked as an oracle-coupled ceiling and cannot be selected as the supervisor improvement arm or used as a policy-promotion source.
P5. Leave-one-reviewer-out analysis will record reviewer marginal effect and reviewer correlation indicators when reviewer-level decisions are available, and will mark the analysis unavailable rather than fabricate it when data is missing.
P6. Gaming flags, reviewer-panel unavailability, insufficient sample size, or unmet powered thresholds will keep `metric_applyable=false` and prevent any applyable policy proposal.
P7. A powered-threshold-met report may mark the metric as applyable evidence, but it will still keep `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.
P8. The report will export replayable artifact references and observational trend rows without granting gate authority or causing automatic policy mutation.

## Implementation Decisions

Use the mergeability bench module as the deep module seam because it already owns candidate loading, oracle isolation, paired acceptance metrics, full-gate reviewer packets, live-generation reports, confidence intervals, and report-only invariants. Add a public factorial report function that accepts precomputed per-arm decisions and reviewer-level rows for deterministic tests, while leaving live generation and external model invocation outside the default test path. Reuse existing acceptance-arm summary, Wilson interval, matched true-accept, oracle leak, and policy-derivation guardrail vocabulary where possible. Trend export should be represented as an observational payload, not a ledger write from the pure report function.

## Testing Decisions

Tests will start at the public factorial report interface, not helper-only math functions, because the promise is a replayable report with promotion guardrails. Fake arm decisions and reviewer-level decisions are acceptable fixtures because external model generation and live reviewer calls are outside the deterministic unit boundary. The first RED test will assert the complete labeled arm set, then subsequent vertical tests will cover candidate-pool equality, matched-TAR refusal, oracle ceiling labeling, leave-one-reviewer-out analysis, gaming guardrails, powered-threshold behavior, reviewer unavailability, replay artifacts, and trend row output.

## Out of Scope

This slice does not run a real powered benchmark, generate new live candidates, expand the corpus, tune prompts, approve policy proposals, mutate overlays, advance gates, or claim that the supervisor has improved in production. It also does not replace the mergeability oracle with real maintainer judgment or make auto-evolve the default. Those remain separate evaluation and operator-approval decisions after the factorial report surface is trustworthy.
