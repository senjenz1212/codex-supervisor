# PRD Grill Findings

### Finding 1: Candidate Changes Must Not Stay Caller-Authored

status: resolved
Resolution: the public boundary is a new deriver that reads report records and derives the overlay candidate ref itself. Existing `create_policy_evolution_proposals(..., candidate_changes=...)` remains as a lower-level apply/proposal helper, but Phase C tests must exercise the no-caller-candidate path.

### Finding 2: Positive Metric Delta Must Be Explicit

status: resolved
Resolution: accepted status alone is insufficient. The report must expose a positive `metric_delta`, or enough before/after metric fields for the deriver to compute a positive delta.

### Finding 3: Overlay-Only Rejection Must Happen At Derivation

status: resolved
Resolution: the deriver rejects or skips non-overlay candidate surfaces before calling proposal creation or writing proposal events. Apply-time guards remain defense in depth.

### Finding 4: Draft Status And Authority Invariants Must Be Asserted

status: resolved
Resolution: derived proposal tests must assert draft-only status and all no-auto-apply authority flags.
