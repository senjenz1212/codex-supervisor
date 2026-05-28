# Supervisor MCP Reliability Implementation Plan

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `supervisor/dual_agent_workflow.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `tests/test_dual_agent_workflow_driver.py`
- `docs/how-to/dual-agent-from-new-chat.md`
- `docs/testing/dual-agent-harness-health-matrix.md`
- `docs/testing/dual-agent-slice0-coverage-index.md`
- `pyproject.toml`

## Risks

- A fix that only works in direct stdio may still leave a stale Desktop MCP
  client wedged until the app is restarted.
- A fallback CLI can be misread as a weaker review path unless documentation
  says it calls the same API and writes the same ledger.
- Cursor review can become too expensive or noisy if every gate is reviewed
  without profile selection.
- Environment loading can accidentally override explicit operator-provided
  variables if the loader is too broad.

## Traceability

- P1 -> test_codex_supervisor_mcp_stdio_tools_call_keeps_protocol_stream_clean
- P2 -> test_workflow_cli_payload_runs_same_supervisor_api
- P2 -> test_workflow_cli_loads_codex_mcp_env_without_overriding_existing
- P3 -> test_cursor_review_gate_profiles_are_policy_not_prompt
- P3 -> test_run_dual_agent_workflow_rigorous_cursor_profile_reviews_quality_gates
- P4 -> test_workflow_cli_payload_runs_same_supervisor_api

## Steps

1. Add a failing subprocess regression test for MCP stdio request logging.
2. Configure MCP transport logging and keep stdout protocol-only.
3. Add the fallback workflow CLI and console script.
4. Load Codex MCP env and supervisor secrets for fallback runs.
5. Add Cursor gate profile selection and workflow route recording.
6. Update operator documentation and coverage indexes.
7. Run focused tests, compileall, diff hygiene, and fallback smoke.
