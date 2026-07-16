# Decision 0001: ClaimGate Owns Improvement Flags

- Status: Accepted
- Date: 2026-07-12

## Context

Historical report producers assigned improvement-authority booleans directly.
Although most assignments were conservatively false, the field remained a
producer-controlled assertion and could later be changed without causal
evidence.

## Decision

`ClaimGate` exclusively derives `improvement_claim_allowed` and
`powered_improvement_claim_allowed` from a cumulative evidence bundle.
Producer input containing either field is an error. L3 is the minimum level
for both outputs.

## Consequences

- Existing report-only producers remain false because they have no qualifying
  L3 evidence bundle.
- A future true value requires a randomized, powered, positive B-vs-C result
  built on L0 through L2 evidence.
- Source tests reject new literal assignments.
- Copying a flag does not transfer authority; consumers must retain the
  evidence lineage.
