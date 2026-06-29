# Report-Only Real Mergeability Benchmark PRD

## Problem Statement

The SWE-bench official all-arms diagnostic can run replayed real-instance rows, but its report does not yet gather the hardened reviewer evidence into the benchmark-facing shape operators need. Without explicit oracle limitation, decision-freeze proof, S_probe-vs-S_full marginal, reviewer provenance, rubric/abstention coverage, and generator-family warnings, a real benchmark run can still be misread as a maintainer-mergeability or policy-improvement claim.

## Solution

Extend the official all-arms diagnostic report with a report-only real-benchmark evidence block. Reuse the existing official replay and bridge machinery, keep hidden oracle material excluded from public artifacts, and add machine-readable fields that state the SWE-bench oracle is a held-out test-pass proxy rather than maintainer mergeability.

## User Stories

1. As an evaluator operator, I want the real benchmark report to state the oracle limitation, so that test-pass evidence is not sold as maintainer mergeability.
2. As an evaluator operator, I want frozen decision proof, so that I can verify reviewer decisions were fixed before oracle scoring.
3. As an evaluator operator, I want S_probe-vs-S_full and reviewer marginal fields, so that the panel effect is visible in the real benchmark report.
4. As an evaluator operator, I want reviewer provenance and abstention coverage, so that reviewer diversity and human-review deferrals are visible.
5. As an evaluator operator, I want generator-family warnings, so that same-family judge/generator risk is not hidden.
6. As an evaluator operator, I want all policy flags false, so that real benchmark evidence remains report-only until powered criteria are met.

## PRD Promise Contracts

- P1 Oracle limitation is explicit.
  - Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
  - Chosen seam/interface: official all-arms report dictionary.
  - Allowed outcomes: report states SWE-bench is a held-out test-pass oracle and `human_mergeability_claim_allowed=false`.
  - Forbidden outcomes: reporting maintainer mergeability or policy improvement.

- P2 Decisions freeze before oracle scoring.
  - Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
  - Chosen seam/interface: official all-arms report dictionary.
  - Allowed outcomes: report includes freeze-before-oracle proof and hidden-field leak checks.
  - Forbidden outcomes: oracle labels influencing reviewer decisions.

- P3 Panel marginal is reportable.
  - Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
  - Chosen seam/interface: bridge report and top-level all-arms aliases.
  - Allowed outcomes: false-accept reduction at matched true-accept, S_probe-vs-S_full, and reviewer marginal are present when computable.
  - Forbidden outcomes: unavailable S_full imputed as accept.

- P4 Reviewer evidence is visible.
  - Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
  - Chosen seam/interface: reviewer provenance, rubric coverage, and generator disjointness report blocks.
  - Allowed outcomes: provenance, abstention coverage, and self-preference warnings are emitted.
  - Forbidden outcomes: text labels becoming scoring authority.

- P5 Report-only invariants hold.
  - Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
  - Chosen seam/interface: top-level policy flags.
  - Allowed outcomes: all policy and improvement flags are false.
  - Forbidden outcomes: deriving a policy proposal from this report.

## Implementation Decisions

- Extend `_build_official_all_arms_diagnostic_report` additively.
- Reuse bridge FAR/TAR/FRR, frozen decision rows, official replay leak checks, and reviewer-result receipts.
- Reuse mergeability reviewer provenance, rubric coverage, and generator disjointness helpers by adapting SWE-bench rows into their existing report shape.
- Add the public rubric to SWE-bench reviewer packets without exposing hidden oracle categories.

## Testing Decisions

- Start with public-boundary tests that call the official all-arms diagnostic runner.
- Test the report fields rather than helper internals.
- Cover computed marginal, oracle limitation, hidden-field proof, rubric coverage, abstention coverage, and same-family warning cases.

## Out of Scope

- Running a costly live SWE-bench dataset fetch.
- Claiming powered improvement.
- Claiming human maintainer mergeability.
- Mutating policy or defaults.
