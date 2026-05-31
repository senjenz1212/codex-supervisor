# Reviewer Unavailable Recovery PRD Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Could degraded recovery accidentally count Cursor as accepting?
resolution: The PRD requires a ledger receipt with
`reviewer_verdict_counted_as_accept=false`; workflow advancement is based only
on Claude and Codex acceptance when the Cursor failure is classified as
recoverable infrastructure.

### Finding G2

status: resolved
severity: high
question: Does the default policy silently weaken independent review?
resolution: Default policy is `escalate`, not `proceed_degraded`. A human
resume signal is required before the default path can proceed degraded.

### Finding G3

status: resolved
severity: high
question: What protects high-stakes gates?
resolution: Agentic-required, runtime-native evidence, and user-facing evidence
paths escalate instead of auto-proceeding even when `proceed_degraded` is
requested.

### Finding G4

status: waived
severity: medium
question: Should this slice diagnose why Cursor misses the contract?
reason: Prompt/model diagnosis is explicitly out of scope; this slice only
changes safe recovery after PR #2's classification has already happened.

## Decision

No open PRD grill findings remain.
