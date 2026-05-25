# Outcome Review: Dual-Agent Trace Diagnostics

## Verdict

status: accepted

The slice meets the PRD promises:

- MAST fields are additive and deterministic.
- All 14 MAST modes are reachable through explicit classifier inputs.
- Replay exports include cross-event sequence diagnostics.
- Trace-envelope tool calls carry timing fields.
- Owned invocation boundaries use supervisor-measured timing.
- Markdown artifacts expose MAST and timing details for human review.

## Evidence

- `tests/test_failure_taxonomy.py` covers full MAST mapping, accepted result
  with missing required probes, and fake-clock timing.
- `tests/test_dual_agent_artifacts.py` covers Markdown projection and replay
  manifest sequence failures.
- `tests/test_dual_agent_runner.py` covers direct gate interaction tool-call
  timing.
- `tests/test_codex_supervisor_mcp_stdio.py` covers MCP round timing.
- Live failure-mode artifacts were refreshed at
  `docs/dual-agent/live-failure-mode-probe-20260525-01/`.

## Validation

- `uv run pytest -q` -> 418 passed
- `python3 -m compileall -q supervisor mcp_tools scripts tests` -> passed
- `git diff --check` -> passed
- diff secret scan -> clean

## Caveats

- `ensure_tool_call_timing` still normalizes legacy untimed records to preserve
  schema shape. Meaningful wall-clock durations should be trusted only for
  invocation boundaries wrapped by `timed_tool_call`.
- MAST is used as a deterministic diagnostic overlay, not as a replacement for
  the supervisor's internal categories.
- Cursor remains a reviewer/challenger; Codex/supervisor remains the final
  lifecycle and acceptance boundary.
