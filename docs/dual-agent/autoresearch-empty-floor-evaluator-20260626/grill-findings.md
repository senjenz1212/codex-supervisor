# PRD Grill Findings

Gate: `prd_review`

Decision: accepted after revisions below.

## Findings

### G1. Seed leakage is the main failure mode

Finding: The PRD must forbid treating `attempt.metric_before=0.0` as an empty-floor measurement. The existing evaluator control fallback at `supervisor/autoresearch/evaluator.py:779-785` can otherwise preserve the seed.

Resolution: P1 now requires an execution-derived empty-floor metric and forbids pending seed fallback. The implementation must pass measured before values into quality controls.

Status: resolved

### G2. The empty-floor run must be overlay-specific

Finding: Measuring an "empty floor" for unrelated changed files could create misleading comparisons. The empty-floor contract is about stripped policy overlay candidates that can feed policy derivation.

Resolution: P1 and implementation decisions scope the empty-floor run to policy overlay candidate artifacts. Non-policy attempts do not fabricate `empty_floor_comparison()`.

Status: resolved

### G3. Draft derivation must not become policy authority

Finding: A measured empty-floor win is necessary evidence for a draft proposal, but it is not approval to mutate policy or advance a gate.

Resolution: P3 repeats the authority invariants and out-of-scope section forbids autonomous apply, benchmark bridge work, and authority flag changes.

Status: resolved

## Gate Receipt

`prd_review` accepted with P1-P3 covering measured floor, live propagation, and draft-only derivation.
