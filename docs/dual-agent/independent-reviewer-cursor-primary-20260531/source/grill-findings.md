# Independent Reviewer Cursor Primary PRD Grill Findings

## Findings

### Finding G1: Primary Cursor is not safe without timeout enforcement

status: resolved
severity: high
question: Can Cursor become primary based only on the minimal successful probe?
resolution: No. The realistic prompt-size probe did not return within the
requested 180 second envelope. The PRD now requires supervisor-side timeout
enforcement and diagnostics in the same slice as the default flip.

### Finding G2: Fallback must not erase assurance differences

status: resolved
severity: high
question: Could Gemini fallback be mistaken for equivalent agentic review?
resolution: The PRD treats Gemini as a text-only, lower-assurance fallback and
requires the runtime and fallback reason to be recorded on persisted verdicts.

### Finding G3: Rename must preserve compatibility

status: resolved
severity: medium
question: Could renaming `cursor_review` break existing workflow readers?
resolution: The PRD requires an `independent_reviewer` boundary while keeping
`cursor_review` as a backward-compatible alias during migration.

### Finding G4: Safety algebra must stay independent of runtime

status: resolved
severity: high
question: Could degraded fallback weaken valid reviewer rejections?
resolution: The PRD states that valid `revise` or `deny` outcomes block
regardless of whether they came from Cursor SDK or Gemini fallback, and that
both-fail cases route to existing reviewer-unavailable recovery.

## Decision

No open PRD grill findings remain.
