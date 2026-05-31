# Issues: Agentic Lead Provenance Foundation

## Issue 1: Policy Plumbing And Defaults

- PRD promises: ALP-001
- Public boundary: `codex_supervisor_mcp`
- First RED test: `run_dual_agent_workflow` defaults `agentic_lead_policy` to `off` and does not require subagent receipts for `lead_direct`.
- Implementation: wire policy fields through config, MCP tool signatures, workflow CLI payloads, runner specs, and handoff packet policy.

## Issue 2: Supervisor-Derived Evidence Grade

- PRD promises: ALP-002, ALP-003, ALP-004
- Public boundary: `dual_agent_slice0`
- First RED test: a receipt that declares `runtime_native` but lacks supervisor-owned replay-verified refs is downgraded and blocked when `required_evidence_grade=runtime_native`.
- Implementation: derive grade in `dynamic_workflow_receipts.py`; extend dynamic subagent receipts with runtime fields; keep existing `_verify_ref_hash` replay path.

## Issue 3: Required Policy Enforcement

- PRD promises: ALP-002, ALP-006
- Public boundary: `codex_supervisor_mcp`
- First RED test: `agentic_lead_policy=required` blocks before `/lead` when required roles are missing, subagent count is below `min_subagents`, an independent reviewer says revise/deny, or solo execution is not excepted.
- Implementation: feed policy into P13 and return a blocked gate result with details.

## Issue 4: Reliability Controls

- PRD promises: ALP-005
- Public boundary: `dual_agent_runner`
- First RED test: orphaned/stale worker metadata produces cleanup actions and durable log refs without enabling live fan-out.
- Implementation: add a small worker-control helper for timeout/budget/log metadata and orphan cleanup.

## Issue 5: Eval Harness

- PRD promises: ALP-007
- Public boundary: `replay_cli`
- First RED test: representative task rows produce a comparison report for `lead_direct`, `agentic_allowed`, and `agentic_required`.
- Implementation: add deterministic report generation; do not change defaults based on it in this slice.
