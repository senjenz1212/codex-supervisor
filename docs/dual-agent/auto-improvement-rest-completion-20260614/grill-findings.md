# PRD Grill Findings

## G1: "Drain" must alter queue state, not only write a decision event
Resolved in P1: the public boundary must expose `experiments park` and the state row must leave the open draft/runnable pool.

## G2: Per-task-class overlay must preserve existing global overlay semantics
Resolved in P2: global `instruction_guidance_blocks` remains supported; task-class overlays are additive and scoped.

## G3: Freeze cannot become a hidden gate
Resolved in P3: freeze is instruction-suppression only and must record `gate_authority=unchanged`.

## G4: Empty-floor evidence must not depend on hand-entered metrics
Resolved in P4: the comparison must be represented as evaluator-derived evidence, or the proposal is non-applyable.

## G5: D1/D2 are operational decisions, not source features
Resolved in P6: the implementation can expose enough measurements, but final promotion is allowed only after sufficient trend rows exist.

