# TDD Grill Findings

## Finding 1

status: resolved

Concern: The first test could accidentally prove only helper-level classification
instead of the public trace-envelope boundary the operator reads.

Resolution: Keep the MAST classifier test, but also assert
`stamp_trace_envelope` changes accepted-but-incomplete probe payloads into a
blocked policy verdict with `FM-3.1`.

## Finding 2

status: resolved

Concern: Timing tests could pass with synthetic zero-duration records while
real invocation boundaries remain unmeasured.

Resolution: Use a fake-clock unit test for the helper, then assert runner and
MCP-produced trace envelopes carry timing on `start_dual_agent_gate`,
`invoke_claude_lead`, planning validation, handoff writing, and probe
evaluation tool calls.

## Finding 3

status: resolved

Concern: Sequence failure tests could overfit to a single event payload and miss
the replay-manifest requirement.

Resolution: Add an artifact-export test that builds multiple ledger events and
asserts `replay/manifest.json` contains `FM-1.3`, `FM-2.5`, `FM-3.1`, and
`FM-3.3` sequence diagnostics with the source event ids.

## Finding 4

status: resolved

Concern: Cursor SDK duration and supervisor wall-clock duration can be confused
if both use `duration_ms`.

Resolution: Keep `duration_ms` as the supervisor-measured tool-call duration
inside `trace_envelope.tool_calls`, and store Cursor's own reported duration as
`cursor_duration_ms` on the tool-call record.
