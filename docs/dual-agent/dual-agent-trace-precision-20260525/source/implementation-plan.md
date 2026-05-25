# Implementation Plan: Dual-Agent Trace Precision Hardening

## Files

- `supervisor/trace_envelope.py`
- `supervisor/dual_agent_lead.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/dual_agent_artifacts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `scripts/probe_live_failure_mode.py`
- `tests/test_failure_taxonomy.py`
- `tests/test_dual_agent_lead_invoker.py`
- `tests/test_dual_agent_runner.py`
- `tests/test_dual_agent_artifacts.py`

## Steps

1. Add RED tests for microsecond timing, stable ids, duplicate references,
   token parsing, parent ids, artifact tables, and MAST trigger coverage.
2. Extend trace-envelope normalization with `duration_us` and `tool_call_id`.
3. Parse Claude token usage at the lead invocation boundary.
4. Add token fields and parent ids to runner/MCP tool calls.
5. Render relationship and token fields in triage/transcript markdown tables.
6. Refresh live Claude + Cursor failure-mode probe artifacts.
7. Run focused tests, full tests, compileall, diff checks, and secret scans.

## Traceability

- P1 -> timing/id trace-envelope tests
- P2 -> duplicate-reference id test
- P3 -> runner parent id test and artifact table render
- P4 -> Claude lead token parsing test and live artifact refresh
- P5 -> all-MAST-trigger coverage test
