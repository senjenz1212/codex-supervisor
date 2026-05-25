# TDD Plan: Dual-Agent Trace Precision Hardening

## RED 1 - Microsecond Timing And Stable IDs

Boundary: `timed_tool_call` / `stamp_trace_envelope`
Test file: `tests/test_failure_taxonomy.py`

Behavior:

- fake monotonic clock advances by a known value
- assert `duration_ms`, `duration_us`, and `tool_call_id`
- assert duplicate call references generate the same `tool_call_id`

## RED 2 - Claude Token Usage

Boundary: `invoke_claude_lead`
Test file: `tests/test_dual_agent_lead_invoker.py`

Behavior:

- feed fixture-shaped Claude JSON with `usage` and `modelUsage`
- assert `tokens_in`, `tokens_out`, component usage, context window, and max
  output tokens are captured

## RED 3 - Parent Tool Calls

Boundary: `run_dual_agent_gate`
Test file: `tests/test_dual_agent_runner.py`

Behavior:

- run a fixture-backed gate
- assert P1/P2/P3 validator tool calls point to the Claude invocation via
  `parent_tool_call_id`
- assert token counts appear on the Claude invocation tool call

## RED 4 - Human-Readable Tables

Boundary: `export_dual_agent_run_artifacts`
Test file: `tests/test_dual_agent_artifacts.py`

Behavior:

- export a blocked run
- assert `triage.md` shows tool call ids, parent/reference ids,
  microsecond duration, token counts, args, and result summaries

## RED 5 - MAST Trigger Coverage

Boundary: `failure_taxonomy_for_payload` and `detect_sequence_failures`
Test file: `tests/test_failure_taxonomy.py`

Behavior:

- trigger direct payload modes through failure payloads
- trigger sequence modes through event lists
- assert observed MAST codes equal the 14-mode registry
