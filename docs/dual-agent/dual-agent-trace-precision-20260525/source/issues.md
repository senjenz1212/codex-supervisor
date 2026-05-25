# Issues: Dual-Agent Trace Precision Hardening

## Issue 1 - Add Microsecond Tool Timing

PRD promise: P1
Public boundary: `supervisor.trace_envelope.timed_tool_call`
Priority: P0
Estimate: S

Acceptance Criteria:

- [ ] `duration_ms` remains present and integer-compatible.
- [ ] `duration_us` is present on timed and normalized calls.
- [ ] Existing artifacts remain backward compatible.

## Issue 2 - Add Stable Tool Call IDs

PRD promise: P2
Public boundary: `supervisor.trace_envelope.stamp_trace_envelope`
Priority: P0
Estimate: S

Acceptance Criteria:

- [ ] Every trace tool call has `tool_call_id`.
- [ ] Duplicate references with the same name/start/duration keep the same id.
- [ ] Existing caller-supplied ids are preserved.

## Issue 3 - Add Tool Call Relationships

PRD promise: P3
Public boundary: `run_dual_agent_gate` and live probe artifact export
Priority: P1
Estimate: M

Acceptance Criteria:

- [ ] Runner validators reference the Claude invocation with
  `parent_tool_call_id`.
- [ ] Re-rendered claim verification calls can use `references_tool_call_id`.
- [ ] Markdown tables show both relationship fields.

## Issue 4 - Capture Claude Token Usage

PRD promise: P4
Public boundary: `supervisor.dual_agent_lead.invoke_claude_lead`
Priority: P0
Estimate: S

Acceptance Criteria:

- [ ] `usage` and `modelUsage` fields are parsed when present.
- [ ] Tool calls include `tokens_in`, `tokens_out`, and component usage.
- [ ] Missing usage remains `None` rather than guessed.

## Issue 5 - Prove MAST Trigger Coverage

PRD promise: P5
Public boundary: `failure_taxonomy_for_payload` and `detect_sequence_failures`
Priority: P1
Estimate: M

Acceptance Criteria:

- [ ] All 14 MAST codes appear in trigger-shaped tests.
- [ ] Cross-event modes include duplicate gate, ignored objection, premature
  accept, and Cursor disagreement scenarios.
