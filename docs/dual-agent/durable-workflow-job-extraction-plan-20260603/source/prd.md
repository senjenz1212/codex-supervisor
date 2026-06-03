# PRD: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

## Problem Statement

The durable workflow-job lifecycle is implemented inside
`mcp_tools/codex_supervisor_stdio.py` next to the MCP method declarations, gate
driver, reviewer wiring, planning validation, artifact export, and transcript
readers. That makes the job transport boundary hard to reason about: submit,
poll, catch-up, idempotent reattach, detached CLI spawning, ledger terminal
outcome persistence, and terminal discrepancy reconciliation are spread across
the same large adapter module. The operator needs an evidence-backed extraction
plan before any source refactor, so the next implementation slice can move code
without weakening the durable transport contract.

## Solution

Use four read-only investigation lanes to inventory the extraction surface and
write one design document under `docs/`. The document proposes a
`supervisor/durable_workflow_job.py` module boundary, keeps the MCP tool methods
as thin adapters, lists what moves and what stays, maps call-site impact, names
the current tests that pin behavior, and records risks. This run produces only
documentation and supervised evidence; it does not move source code.

## User Stories

- As a maintainer, I can see the exact durable workflow-job responsibilities
  before separating them from the MCP stdio adapter.
- As an operator, I can verify that idempotent submit, poll, catch-up, terminal
  ledger reconciliation, and detached CLI fallback keep their current behavior.
- As a reviewer, I can compare the proposed move list against existing tests
  instead of relying on a broad "extract helper" claim.
- As the next implementer, I can pick up a concrete plan that says which
  functions move, which MCP wrappers stay, and which tests must stay green.

## PRD Promise Contracts

P1. Durable Job Surface Inventory

- Public boundary: `docs/durable-workflow-job-extraction-plan.md`.
- Allowed outcomes: the plan enumerates the lifecycle touched by
  `submit_dual_agent_workflow_job`, `poll_dual_agent_workflow_job`,
  `catch_up_dual_agent_workflow`, `run_dual_agent_workflow`, the detached CLI,
  `client_token` idempotency, terminal-outcome ledger persistence, and event
  replay.
- Forbidden outcomes: omitting terminal reconciliation, treating the CLI worker
  as unrelated, or claiming the MCP methods can disappear.

P2. Behavior-Preserving Module Boundary

- Public boundary: the move-vs-keep table in the design document.
- Allowed outcomes: implementation logic moves to
  `supervisor/durable_workflow_job.py`; MCP methods stay in
  `mcp_tools/codex_supervisor_stdio.py` as thin adapters; `State` remains the
  durable storage owner.
- Forbidden outcomes: changing gate semantics, changing the state schema in
  this plan, or moving MCP registration out of the adapter.

P3. Test Inventory As Safety Net

- Public boundary: the design document's behavior-pinning test inventory.
- Allowed outcomes: the plan names existing tests for detached spawn, payload
  round-trip, explicit and derived idempotency, concurrent dedupe, catch-up,
  reconnect, ledger terminal outcomes, cache discrepancy handling, migrations,
  and required fan-out receipts.
- Forbidden outcomes: planning an extraction without naming tests that prove
  submit, poll, catch-up, and terminal behavior remain equivalent.

P4. Read-Only Fan-Out Evidence

- Public boundary: supervised workflow transcript and agentic worker receipts.
- Allowed outcomes: four read-only workers split call-site mapping,
  dependency/import graph, move-vs-keep boundary, and test inventory; the lead
  synthesizes the documentation.
- Forbidden outcomes: workers writing source files, workers bypassing the lead,
  or counting missing worker receipts as success.

P5. No Source Refactor In This Run

- Public boundary: git diff and committed files.
- Allowed outcomes: only documentation and dual-agent planning artifacts change.
- Forbidden outcomes: moving Python code, changing config defaults, altering
  `agentic_lead_policy`, or modifying `supervisor/state.py`.

## Implementation Decisions

- Treat this as a design-doc slice, not a code extraction slice.
- Use the existing docs convention for design material by placing the durable
  plan directly under `docs/` and keeping supervised planning artifacts under
  `docs/dual-agent/<task_id>/source/`.
- Keep `State` methods in `supervisor/state.py` for now because they own SQLite
  schema, migrations, and atomic terminal-outcome writes.
- Keep `codex_supervisor_workflow_cli.py` as the process entrypoint, but route
  its durable result-persistence helper through the extracted module in the next
  slice.
- Preserve current MCP tool names and payload shapes so existing clients do not
  need a transport migration.

## Testing Decisions

- This run validates by documentation review, planning-artifact validation, and
  a targeted pytest set around durable workflow jobs and event ledger behavior.
- The first safety tests are the existing public-boundary tests in
  `tests/test_dual_agent_workflow_driver.py` and `tests/test_state_event_ledger.py`.
- The gated workflow must run with `agentic_lead_policy="required"` and
  `min_subagents=4` so the read-only worker evidence is recorded through the
  same supervisor path that the plan describes.
- No live source refactor test is added because source behavior is intentionally
  unchanged.

## Out Of Scope

This slice does not create `supervisor/durable_workflow_job.py`, move Python
functions, change the `dual_agent_workflow_jobs` schema, alter terminal-outcome
redaction, change MCP method names, modify reviewer or gate semantics, change
fan-out defaults, or tune agentic worker limits. Those are separate
implementation decisions for the follow-up extraction slice.
