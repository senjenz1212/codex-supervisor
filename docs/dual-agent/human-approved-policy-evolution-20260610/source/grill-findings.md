# PRD Grill Findings

### Finding 1: Accepted Evidence Is Not Approval

Status: resolved

- Risk: accepted AutoResearch output could be mistaken for permission to mutate.
- Resolution: proposal creation is read-only; approval has a separate operator identity and channel.

### Finding 2: Diff Must Be Replayable

Status: resolved

- Risk: a proposal without before/after hashes is not auditable.
- Resolution: proposal changes include target path, candidate ref, before hash, after hash, and unified diff.

### Finding 3: Gaming Flags Block Applyability

Status: resolved

- Risk: accepted records can still carry warning flags.
- Resolution: records with any gaming flag create no applyable proposal.

### Finding 4: Rollback Needs Stored Bytes

Status: resolved

- Risk: rollback may restore the wrong artifact if it depends on memory.
- Resolution: approval stores rollback backup bytes and a pointer.

### Finding 5: Gate Authority Stays Separate

Status: resolved

- Risk: proposal events could be confused with gate decisions.
- Resolution: every payload states that gate, reviewer panel, and typed outcome authority are unchanged.

## Waivers

- None.
