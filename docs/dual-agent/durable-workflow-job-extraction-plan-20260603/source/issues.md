# Issues: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

## Slice 1: Durable Job Surface Inventory

Priority: P1

Scope: Inventory the public tool methods, helper functions, CLI fallback, state
methods, event kinds, and tests that define durable workflow-job behavior.

Acceptance Criteria:

- [ ] The plan names the public tool methods and how they interact.
- [ ] The plan names the CLI worker and terminal persistence helper.
- [ ] The plan identifies `State` storage methods and ledger event kinds.
- [ ] The plan includes call-site impact for MCP clients, CLI callers, tests,
  and artifact/transcript readers.

PRD promise: P1

## Slice 2: Move-Vs-Keep Boundary

Priority: P1

Scope: Propose the `supervisor/durable_workflow_job.py` boundary, separating
portable lifecycle logic from MCP registration and SQLite ownership.

Acceptance Criteria:

- [ ] The move list includes idempotency token generation, request payload
  assembly, detached spawn orchestration, poll reconciliation, and catch-up
  assembly.
- [ ] The keep list leaves MCP method decorators and `State` persistence in
  their current modules.
- [ ] The plan preserves existing payload shapes and tool names.
- [ ] The plan calls out the CLI helper migration without changing it now.

PRD promise: P2

## Slice 3: Behavior-Pinning Test Inventory

Priority: P1

Scope: Name the current tests that must pin behavior during the eventual
source extraction.

Acceptance Criteria:

- [ ] Submit, poll, catch-up, reconnect, and terminal-outcome tests are listed.
- [ ] Idempotency tests cover explicit, derived, distinct, and concurrent
  tokens.
- [ ] State migration and event-tail tests are listed.
- [ ] Required fan-out receipt tests are listed as adjacent safety coverage.

PRD promise: P3

## Slice 4: Supervised Doc-Only Delivery

Priority: P1

Scope: Write the design document, validate planning artifacts, run targeted
tests, submit the durable fan-out workflow with four read-only workers, and
commit only documentation changes.

Acceptance Criteria:

- [ ] Four read-only worker receipts are available in the gated workflow.
- [ ] The gated workflow accepts through outcome review or records only minor
  reviewer findings.
- [ ] The committed diff contains no source refactor.
- [ ] The final report includes fan-out signal fields and confirms per-call
  `agentic_lead_policy="required"` did not change config defaults.

PRD promise: P4, P5
