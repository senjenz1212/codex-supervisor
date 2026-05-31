# PRD Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Could dynamic workflow become a replacement supervision layer?
resolution: Keep the canonical term `execution-layer preview`. Codex plus Claude Code `/lead`, the supervisor ledger, P13, P11, and exported artifacts remain the acceptance surface.

### Finding G2

status: resolved
severity: high
question: Could a model attest that receipts exist without replay evidence?
resolution: P13 must open files and recompute hashes for replay refs. A model saying receipts exist is not evidence.

### Finding G3

status: resolved
severity: high
question: Could N-agent synthesis hide disagreement behind a quorum?
resolution: Registered subagents can produce accept/revise/block decisions, but any critical or important blocking objection prevents acceptance until resolved. No quorum can override critical evidence.

### Finding G4

status: resolved
severity: medium
question: Does same-API resume overclaim raw MCP transport reconnection?
resolution: This slice makes recovery durable by letting the operator re-run the same workflow and resume from existing accepted gates. Raw MCP transport retry is documented as a later integration slice.

### Finding G5

status: resolved
severity: medium
question: Should `claude_agent_sdk` be required for test collection?
resolution: `supervisor.agent_invoker` may require the Claude Agent SDK only when it actually invokes an agent.

### Finding G6

status: resolved
severity: high
question: Does same-API resume fully solve long MCP tool-call transport failure?
resolution: No. Same-API resume handles reruns after accepted gates. Long-call transport recovery also needs a submit/poll job path where the initiating MCP call returns quickly and a detached CLI worker continues the workflow.

## Decision

No open findings remain.
