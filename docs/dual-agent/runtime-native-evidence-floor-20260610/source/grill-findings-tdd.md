# TDD Grill Findings

### Finding 1 - Boundary Tests Come First

Status: resolved

The first tests exercise `run_dual_agent_workflow`, the same public boundary used by the durable workflow job. Helper-level checks can support the implementation after the boundary behavior is pinned.

### Finding 2 - Runtime Evidence Must Be Durable

Status: resolved

The TDD plan includes a transcript test that verifies runtime evidence is written to the ledger and replayed through `read_gate_transcript`.

### Finding 3 - Agent Receipt Regression Must Be Direct

Status: resolved

The TDD plan includes negative tests where fabricated or agent-only receipts fail despite being present in the receipt list.
