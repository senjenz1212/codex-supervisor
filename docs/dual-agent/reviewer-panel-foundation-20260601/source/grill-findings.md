# PRD Grill Findings: Reviewer Panel Foundation

## Findings

### Finding G1: Panel foundation must not imply panel aggregation

   - Risk: A list-shaped schema could accidentally change the gate decision.
   - Resolution: PRD P4 explicitly forbids aggregation changes; the current
     single reviewer remains the decision source for this slice.
Status: resolved

### Finding G2: Legacy Cursor-named events are durable audit artifacts

   - Risk: Renaming `tri_agent_cursor_review` directly would break transcript,
     artifact, and replay consumers.
   - Resolution: P3 requires dual-write/read compatibility and an ADR.
Status: resolved

### Finding G3: Mock reviewers are required to validate N-panel shape

   - Risk: Blocking on a second real reviewer delays the schema foundation.
   - Resolution: P2 requires mock reviewers plus the one real working
     Gemini/LiteLLM adapter.
Status: resolved

### Finding G4: Lineage must be explicit enough for future weighting

   - Risk: Future aggregation cannot reason about reviewer dependence if the
     schema stores only runtime names.
   - Resolution: P1 requires provider family or lineage, tool access, assurance
     grade, confidence, severity, transcript refs, and hashes.
Status: resolved

## Decision

Proceed to issue slicing. All findings are resolved in the PRD contracts and
out-of-scope boundaries.
