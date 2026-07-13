# PRD: PROGRAM-001 Harness v1 Claim Kernel

## Problem Statement

Supervisor reports currently carry improvement-authority flags as producer
fields. Most are hardcoded false, which is conservative, but the architecture
still permits a future producer to assert improvement without proving the
required evidence chain. Existing fixture, replay, mergeability, and
AutoResearch artifacts also lack one shared hierarchy that distinguishes
integrity, process, outcome, causal, portable, ROI, and auto-improvement
claims.

## Solution

Create the Harness v1 program pack and a single evidence-derived `ClaimGate`.
The gate evaluates a cumulative L0–L6 ladder, owns both improvement-authority
flags, rejects producer overrides at any depth, and rejects registered report
claims above the evidence level. Existing report-only producers obtain their
false flags from the gate rather than literals. Historical artifacts are
mapped by reference without upgrading their evidentiary authority.

## User Stories

1. As an operator, I want a report to state the strongest claim its evidence
   supports so I can distinguish diagnostics from verified outcomes.
2. As an evaluator, I want an independent hidden verifier to be mandatory for
   L2 so public or self-reported checks cannot masquerade as outcomes.
3. As an experiment owner, I want causal improvement to require a randomized
   powered B-vs-C result so extra compute is not confused with harness value.
4. As a maintainer, I want report producers unable to set improvement flags so
   authority cannot drift through a local boolean change.
5. As a reviewer, I want forbidden claims rejected with required and available
   levels so unsupported prose fails closed.
6. As a program lead, I want legacy artifacts referenced in the new graph
   without rewriting history or overstating what they proved.

## PRD Promise Contracts

P1. Cumulative Claim Ladder

- User-visible promise: an evidence bundle resolves to exactly the highest
  fully satisfied L0–L6 level.
- Public boundary: `ClaimGate.max_claim_level`.
- Allowed outcomes: missing evidence stops at the prior level; no L0 evidence
  returns no authorized level.
- Forbidden outcomes: skipping a missing lower level or accepting bare
  self-declared booleans as evidence.

P2. Independent Hidden Outcome Verification

- User-visible promise: L2 is impossible without an independent hidden
  verifier receipt tied to a pinned result.
- Public boundary: `ClaimGate.max_claim_level`.
- Allowed outcomes: a valid verifier record raises L1 to L2.
- Forbidden outcomes: public checks, non-independent review, or unpinned
  results reaching L2.

P3. Derived Improvement Authority

- User-visible promise: both improvement flags are false below L3 and true only
  when the evidence bundle reaches L3 or above.
- Public boundary: `ClaimGate.derive_report`.
- Allowed outcomes: the gate injects both fields and a derivation receipt.
- Forbidden outcomes: producer input sets either field at any nesting depth.

P4. Forbidden Claim Rejection

- User-visible promise: a governed report cannot assert a registered claim
  above its evidence level.
- Public boundary: `ClaimGate.validate_report`.
- Allowed outcomes: supported claim IDs or phrases pass; unsupported and
  unknown claims raise a typed error.
- Forbidden outcomes: trusting a report-supplied required level or silently
  accepting "supervisor improves outcomes" on fixture evidence.

P5. Program and Legacy Traceability

- User-visible promise: the charter, ladder, claims, forbidden statements,
  architecture, roadmap, and legacy references are reviewable in one pack.
- Public boundary: repository program documentation and YAML parsing.
- Allowed outcomes: historical artifacts remain at original paths with explicit
  authority caveats.
- Forbidden outcomes: rewriting old reports or treating a planning artifact as
  runtime evidence.

## Implementation Decisions

- Place the deep module at the report-authorization seam rather than inside an
  individual benchmark producer.
- Use stable SHA-256-bearing evidence records and cumulative predicates.
- Require a positive randomized powered B-vs-C result for L3 improvement
  authority.
- Keep the runtime claim registry in code and mirror it in YAML so installed
  packages do not depend on repository-relative docs.
- Scan production Python for literal managed-flag assignments.
- Migrate report-only producers conservatively with an empty causal evidence
  bundle; no existing report is upgraded.

## Testing Decisions

- Begin at public `ClaimGate` interfaces, not private predicate helpers.
- Use one RED→GREEN cycle per level or behavior.
- Test both the positive hidden-verifier path and refusal without independence.
- Test top-level and nested manual flag rejection.
- Test a fixture-replay L1 bundle refusing an asserted L3 claim.
- Test the runtime/YAML claim registry and producer source scan.

## Out of Scope

- Building the TRACE, LEDGER, GRADE, runtime, task, or experiment kernels.
- Declaring any existing report causal evidence.
- Changing policy mutation or deployment authority.
- Running the AXI workflow, external reviewers, or committing the branch in
  this direct implementation session.
