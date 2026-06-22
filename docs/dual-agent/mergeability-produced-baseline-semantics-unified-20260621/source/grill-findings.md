# PRD Grill Findings

## Findings

### Finding 1: Legacy self-report must not remain a fallback

Status: resolved

The plan now preserves `baseline_self_report` only as calibration context and requires produced receipts for baseline availability.

### Finding 2: The test seam must be public

Status: resolved

The test plan starts at bridge and runner reports rather than helper-only receipt parsing.

### Finding 3: Live generation must not fabricate baseline decisions

Status: resolved

The live path attaches produced baseline evidence only when the generator supplies an explicit accept or reject decision.
