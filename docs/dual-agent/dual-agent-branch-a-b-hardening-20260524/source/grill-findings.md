# Dual-Agent Branch A+B Hardening Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Could the workflow still claim PRD/TDD discipline if Codex only writes PRD-looking files?
resolution: `run_dual_agent_workflow` now defaults to `require_skill_receipts=true` and blocks with P12 before `prd_review` unless skill receipts cover the PRD-to-TDD chain.

### Finding G2

status: resolved
severity: high
question: Could a blocked gate remain too vague for postmortem review?
resolution: dual-agent events receive a trace envelope with deterministic failure taxonomy. P11, P12, P_planning, P2, P3, and Cursor failures classify into system design, governance, task verification, tool execution, or inter-agent misalignment categories.

### Finding G3

status: resolved
severity: medium
question: Could Codex keep rubber-stamping with unexplained confidence values?
resolution: gate-round confidence now uses the same `ConfidenceReport` used in the Codex mailbox message, and the message carries a structured review packet.

### Finding G4

status: waived
severity: medium
question: Should this slice run a green live Cursor SDK probe?
reason: `cursor_sdk` is installed, but `CURSOR_API_KEY` is not present in the current process environment. The slice adds an honest probe script and may record a skipped diagnostic; green live Cursor remains a credentialed follow-up.

## Decision

Proceed with deterministic supervisor hardening now. Do not claim live Cursor or Desktop GUI proof unless a real credentialed/UI probe is run and captured.
