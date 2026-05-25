# Implementation Plan: Dual-Agent Fast Triage Artifacts

## Files

- `supervisor/trace_envelope.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/dual_agent_artifacts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `scripts/probe_live_failure_mode.py`
- `tests/test_failure_taxonomy.py`
- `tests/test_dual_agent_runner.py`
- `tests/test_dual_agent_artifacts.py`
- `tests/test_dual_agent_live_lead_fixture.py`

## Steps

1. Add RED tests for `triage.md`, workspace snapshot manifest, tool-call
   forensic fields, and timed error capture.
2. Extend `timed_tool_call` and normalization helpers without breaking existing
   timing fields.
3. Add safe args/result summaries to owned tool-call boundaries.
4. Add `triage.md` and source artifact links to artifact export.
5. Add workspace snapshot manifest generation.
6. Split Claude gate status and supervisor final status in live probe payloads.
7. Add timeout/no-outcome primary-failure regression so P2/P3 are not
   overwritten by P11.
8. Run focused tests, full tests, compileall, diff checks, secret scans.
9. Refresh live Claude + Cursor probe evidence.

## Traceability

- P1 -> triage export test
- P2 -> runner tool-call forensic test and timed error test
- P3 -> live probe status split inspection
- P4 -> workspace snapshot manifest test
- P5 -> index source links test
- P6 -> timeout/no-outcome final-failure tests
