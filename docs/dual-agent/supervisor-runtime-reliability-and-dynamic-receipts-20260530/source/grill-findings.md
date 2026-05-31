# Grill Findings: PRD Gate

### Finding G1: Reliability must not duplicate the MCP transport fallback slice

Status: resolved

Challenge: The previous reliability slice already added a CLI fallback for MCP transport failures. This PRD must target daemon/runtime survival, not re-implement the same fallback.

Decision: Scope P1 and P2 to restartable runtime loops, rollout watcher callback failures, and health events. The existing `codex_supervisor_workflow_cli.py` path remains unchanged.

### Finding G2: Hook server failures are not equivalent to watcher failures

Status: resolved

Challenge: Restarting every subsystem can hide a fatal hook bind/config problem and make the operator think the supervisor is healthy.

Decision: Hook server remains a fatal daemon task in this slice. Restartable wrapping is limited to watcher/poller/detector/invoker/monitor loops.

### Finding G3: Dynamic workflow is an execution layer, not a supervision layer

Status: resolved

Challenge: The term dynamic workflow adoption could accidentally mean native subagents replace Codex gates or Claude `/lead` integration.

Decision: P4 uses ADR 0003 language: dynamic workflow preview is allowed only behind the lead worker and must produce receipts before acceptance.

### Finding G4: New events must be readable, not only writable

Status: resolved

Challenge: `State.write_event` can store arbitrary event kinds, but read-side allowlists can make new events disappear from transcript and artifact views.

Decision: P4 requires `dual_agent_dynamic_workflow_receipt_validation` to be added to read-side allowlists and artifact renderers.

### Finding G5: P13 must be deterministic

Status: resolved

Challenge: A model reviewer saying dynamic workflow looked safe does not satisfy the user's agentic and receipt requirement.

Decision: P13 is an LLM-free validator over machine-readable receipts and workflow policy.
