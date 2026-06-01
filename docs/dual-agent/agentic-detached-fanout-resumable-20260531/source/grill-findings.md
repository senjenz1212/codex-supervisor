# PRD Grill Findings

### Finding 1: Resume safety must be grounded before planning tests

Status: resolved

The resume/re-attach path was read before TDD. Detached submit reserves by
idempotency token and the CLI calls the same workflow request, but
`run_dual_agent_workflow` only uses caller-supplied `tool_receipts` before
calling `produce_agentic_worker_receipts`. It does not hydrate prior worker
receipts from the ledger or `.handoff/agentic-workers`. The PRD now names
durable hydration as a core promise rather than assuming the existing
skip-existing path is already fed on resume.

### Finding 2: Reattach and restart are different failure modes

Status: resolved

S2 reattach prevents duplicate CLI launch for a live detached job. It does not
by itself restart a crashed job or clean up worker processes. The PRD separates
transport-drop reattach, partial worker receipt hydration, and orphan cleanup so
tests can prove each behavior independently without overclaiming crash recovery.

### Finding 3: Worker evidence must remain P13-derived

Status: resolved

The PRD keeps `runtime_native` evidence supervisor-derived from replay-verified
`.handoff` refs. It explicitly forbids counting failed, self/solo, or declared
grades as accepted evidence.

### Finding 4: In-flight progress is useful but bounded

Status: resolved

The PRD asks for lightweight spawned/finished events if cheap. The TDD plan
treats those as catch-up evidence and keeps terminal receipts authoritative for
P13.
