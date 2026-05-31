# PRD Grill Findings

## Finding 1: Native Fan-Out Evidence Is Not High-Stakes Provenance

- status: resolved
- severity: blocking
- related promises: ALP-002, ALP-003
- challenge: Claude Code native helpers expose a task id and an ephemeral output path, but not a stable supervisor-owned transcript contract.
- resolution: high-stakes `agentic_lead_policy=required` must use supervisor-derived evidence grade and replay-verified refs. Native `/lead` fan-out remains allowed only for lower-stakes `lead_captured` evidence.

## Finding 2: The Lead Cannot Grade Itself

- status: resolved
- severity: blocking
- related promises: ALP-003
- challenge: A field like `evidence_grade=runtime_native` in `/lead` output is not evidence.
- resolution: grade assignment belongs in `supervisor/dynamic_workflow_receipts.py`; declared grades are ignored.

## Finding 3: Circuit Breakers Must Precede Live Fan-Out

- status: resolved
- severity: important
- related promises: ALP-005
- challenge: timeout, budget, durable logs, and orphan cleanup after fan-out would make the first live path unbounded.
- resolution: this slice adds policy and verifier controls first; live fan-out remains disabled by default.

## Finding 4: Dynamic Receipts Already Exist

- status: resolved
- severity: important
- related promises: ALP-004
- challenge: a second receipt format would weaken traceability.
- resolution: extend `dynamic_subagent_result` and P13/P14 synthesis.
