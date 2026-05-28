# Supervisor MCP Reliability TDD Plan

## Public Boundary

Use `mcp_tools.codex_supervisor_stdio`, `mcp_tools.codex_supervisor_workflow_cli`,
and `supervisor.dual_agent_workflow`.

## Test Cases

### test_codex_supervisor_mcp_stdio_tools_call_keeps_protocol_stream_clean

Maps to: ISS-1, P1
RED: Spawn `mcp_tools.codex_supervisor_stdio` with JSON-RPC initialize and
`tools/call`, then assert routine request logging does not appear in the
captured protocol streams.
GREEN: Configure MCP transport logging before server construction and request
an error-level FastMCP logger where supported.

### test_workflow_cli_payload_runs_same_supervisor_api

Maps to: ISS-2, P2
RED: Call the fallback payload runner with a fake gate runner and assert no
workflow ledger record or artifact is produced.
GREEN: Route the fallback through `CodexSupervisorMcpAPI.run_dual_agent_workflow`
with the provided config, state, runner, and Cursor runner.

### test_workflow_cli_loads_codex_mcp_env_without_overriding_existing

Maps to: ISS-2, P2
RED: Load a Codex config containing a supervisor MCP env block and assert shell
variables are overwritten or unrelated server variables are imported.
GREEN: Parse only `[mcp_servers.codex_supervisor.env]` and leave already-set
environment variables untouched.

### test_cursor_review_gate_profiles_are_policy_not_prompt

Maps to: ISS-3, P3
RED: Request default, rigorous, vague, and explicit Cursor gate profiles and
observe ambiguous or all-gate behavior.
GREEN: Add deterministic profile mapping and filter selected gates through the
active workflow route.

### test_run_dual_agent_workflow_rigorous_cursor_profile_reviews_quality_gates

Maps to: ISS-3, P3
RED: Run the workflow with `cursor_review_profile="rigorous"` and observe Cursor
review on the wrong set of gates.
GREEN: Record Cursor review events for TDD, implementation plan, and outcome
only.

## RED/GREEN Plan

RED: Add the stdio subprocess regression test for the observed request logging
symptom.
GREEN: Configure MCP transport logging to keep protocol output clean.

RED: Add the fallback workflow API test with fake runners.
GREEN: Add the fallback module and console script that call the same API.

RED: Add Cursor gate policy tests for profile selection and workflow execution.
GREEN: Centralize gate selection in `cursor_review_gates_for_workflow`.
