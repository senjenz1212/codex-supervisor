# Cursor SDK Infrastructure Retry Hardening Issues

## Slice 1: Retry Cursor SDK infrastructure exceptions before fallback

Priority: P0

Scope: Add bounded retry/backoff around Cursor SDK infrastructure exceptions in
`supervisor.cursor_agent.invoke_cursor_agent`. Preserve existing contract retry
and fallback behavior.

PRD promise: P1, P2, P3

Public boundary for first RED test: `supervisor.cursor_agent.invoke_cursor_agent`

Representative action: Fake `_run_cursor_sdk` so it raises once and then returns
a valid typed outcome.

Acceptance criteria:

- [ ] A transient SDK exception is retried and succeeds without invoking fallback.
- [ ] Exhausted SDK exceptions produce typed recoverable
  `reviewer_infrastructure_unavailable` evidence with attempt history.
- [ ] Malformed Cursor output continues to use contract corrective retries.
- [ ] Missing modules are classified without retry loops.

## Slice 2: Thread retry policy through workflow configuration and payloads

Priority: P0

Scope: Add supervisor config defaults and pass retry policy through inline MCP
workflow calls, detached workflow job payloads, and CLI `WORKFLOW_KEYS`.

PRD promise: P4

Public boundary for first RED test:
`mcp_tools.codex_supervisor_workflow_cli.workflow_kwargs_from_payload`

Representative action: Build a workflow payload with retry fields and assert
they are preserved into `CursorInvocationRequest`.

Acceptance criteria:

- [ ] Config defaults are available on `Config.supervisor`.
- [ ] CLI payload filtering preserves retry fields.
- [ ] `run_dual_agent_workflow` passes retry policy to Cursor requests.
- [ ] Detached submit stores retry policy in the job request payload.

## Slice 3: Preserve gate safety and reviewer semantics

Priority: P1

Scope: Add regression coverage that valid Cursor `revise` still blocks and that
reviewer-unavailable recovery still runs only after SDK retries fail.

PRD promise: P2, P5

Public boundary for first RED test:
`mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`

Representative action: Run a workflow with an accepting Claude outcome and a
fake Cursor runner that returns `revise`, then a workflow with a fake Cursor
runner that reports exhausted infra retries.

Acceptance criteria:

- [ ] Cursor `revise` remains blocking.
- [ ] Recovery artifacts still mark missing Cursor verdicts as degraded,
  non-accepting evidence.
- [ ] The retry policy appears in workflow route metadata for audit.
