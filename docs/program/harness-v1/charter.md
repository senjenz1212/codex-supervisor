# Harness v1 Program Charter

## Objective

Build an evidence-first execution harness in which every material claim is
traceable to pinned runtime evidence and no report can authorize a stronger
claim than its evidence bundle supports.

Harness v1 separates seven claim levels:

1. L0 Integrity
2. L1 Process
3. L2 Outcome
4. L3 Causal improvement
5. L4 Portable improvement
6. L5 Positive ROI
7. L6 Safe auto-improvement

The program succeeds only when the evidence path is machine-checkable. Prose,
reviewer confidence, fixture dashboards, report labels, and manually assigned
booleans do not raise a claim level.

## Operating Rules

- Claims are fail-closed. Missing or malformed evidence stops at the highest
  fully satisfied lower level.
- Levels are cumulative. L4 cannot be reached without L0 through L3 and
  replication across at least three pinned model families, including one
  family the optimizer never observed.
- `ClaimGate` owns `improvement_claim_allowed` and
  `powered_improvement_claim_allowed`.
- Report producers must supply an evidence bundle; they may not set either
  managed flag directly at any nesting depth.
- Historical artifacts are referenced through `legacy-map.yaml`; history is
  not rewritten to resemble the new graph.
- Fixture and replay reports can establish integrity or process evidence, but
  they are not causal efficacy evidence.
- A randomized, adequately powered, positive B-vs-C result is the first level
  that authorizes an improvement claim.
- Policy mutation, deployment, and auto-improvement remain separately governed
  even when a claim level is satisfied.

## Current Position

The fixture-replay example in the PROGRAM-001 tests resolves to L1. It is
therefore rejected when it asserts L3. Existing report-only benchmark
producers continue to emit false improvement flags, now derived by
`ClaimGate`; they do not contain the randomized powered B-vs-C evidence needed
for L3.

## Program Definition of Done

- Every claim has a stable ID and minimum evidence level.
- Every report exposes evidence-derived claim authorization.
- Unsupported and unknown registered-report claims fail closed.
- Trace, ledger, grade, runtime, task, experiment, pilot, confirmation,
  optimization, deployment, and scale slices close the path from objective to
  decision.
- A deployment or auto-improvement decision can be reconstructed from pinned,
  immutable evidence without trusting report prose.

## Non-Goals

- Reclassifying historical fixture reports as outcome or causal evidence.
- Treating mergeability acceptance reports as task-efficacy experiments.
- Allowing a manually approved boolean to substitute for evidence.
- Migrating to a durable workflow engine before the efficacy readout justifies
  that investment.
