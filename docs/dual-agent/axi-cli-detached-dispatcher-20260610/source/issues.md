# Issue Slices: Detached Dispatcher And AXI CLI

## Slice AXI-1: Make poll and status non-executing

Priority: P0

Scope: Remove poll-side workflow driving from `mcp_tools/codex_supervisor_stdio.py` and keep poll to durable row reporting, terminal ledger replay, lease fields, and resume hints.

PRD promise: P1, P2

Acceptance criteria:

- [ ] `poll_dual_agent_workflow_job` does not construct `WorkflowJobDispatcher` or call `run_once`.
- [ ] Poll returns `submitted/reserved` or `submitted/request_written` without creating request files or workers.
- [ ] Poll still replays stored `terminal_outcome_json` when the dispatcher or worker has already persisted the result.

## Slice AXI-2: Add AXI CLI over the same core

Priority: P0

Scope: Add `mcp_tools/codex_supervisor_axi.py` and console script `codex-supervisor-axi` with submit, status, poll, catch-up, gates, approve, deny, lessons, and trends commands.

PRD promise: P3, P4, P5

Acceptance criteria:

- [ ] CLI submit calls the same `CodexSupervisorMcpAPI.submit_dual_agent_workflow_job` path as MCP.
- [ ] No-args home, TOON output, `--json`, `--fields`, structured errors, empty states, and `help[]` hints are covered by tests.
- [ ] Forged caller receipts are downgraded and duplicate client tokens reattach to the same durable job.

## Slice AXI-3: Keep dispatcher as the spawn owner

Priority: P0

Scope: Keep request writing, worker spawn, retry, parking, lease heartbeat, and stale lease reaping in `supervisor/workflow_job_dispatcher.py`. Add targeted `--job-id` support for one-shot dispatcher operation.

PRD promise: P2, P6

Acceptance criteria:

- [ ] Dispatcher `--once --job-id <job>` claims that job and writes the request before spawning.
- [ ] Dispatcher tests retain fake process fixtures for request-write and spawn behavior.
- [ ] Dispatcher documentation names the launch command and plist path.

## Slice AXI-4: Preserve read-only trend and lesson inspection

Priority: P1

Scope: Add state read helpers for active jobs/gates and expose lessons/trends through AXI without giving those commands gate authority.

PRD promise: P3, P4

Acceptance criteria:

- [ ] Home view lists active jobs and active gates from the ledger with total counts.
- [ ] Lessons and trends commands are observational and do not advance or block gates.
- [ ] Output remains bounded by default and can be expanded with `--fields` or `--json`.
