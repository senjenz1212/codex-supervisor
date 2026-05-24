# Grill Findings

## Gate

This grill challenges the planning-artifact validator against the existing
dual-agent contract: supervisor ledger as truth, explicit handoff artifacts,
deterministic replay, and no prompt-only lifecycle enforcement.

### Finding G1

status: resolved
severity: high
question: Could section detection alone let Codex write artifact-shaped stubs?
resolution: The validator must combine required sections with minimum body
content, blocked stub phrases, fixture-derived sneaky cases, and an
anti-template/unique-token check. A PRD with headings plus `TBD` bodies fails
even though all headings are present.

### Finding G2

status: resolved
severity: high
question: Could `artifact_policy="relaxed"` bypass planning substance?
resolution: Planning validation lives inside `run_dual_agent_gate`, below MCP
artifact preflight, and always runs for lifecycle-critical gates. Relaxed MCP
preflight can only relax presence/prerequisite checks; it cannot bypass the
runner-level planning validator.

### Finding G3

status: resolved
severity: medium
question: How can implementation-plan traceability be checked without an LLM?
resolution: Require a structured traceability section with PRD promise IDs and
TDD test names. The validator parses referenced IDs and test names and blocks
when any reference does not resolve to the source PRD/TDD documents.

### Finding G4

status: waived
severity: medium
question: Should the supervisor prove the exact `prd-to-tdd` skill process ran?
reason: This slice enforces durable artifacts rather than process provenance.
The next provenance slice can add skill-run receipts, but blocking on artifact
substance closes the current hack path without depending on hidden model traces.

## Decision

All open findings are resolved or waived with reasons. No finding remains open,
so implementation can proceed.
