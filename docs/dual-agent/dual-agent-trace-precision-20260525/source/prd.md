# PRD: Dual-Agent Trace Precision Hardening

## Problem

The dual-agent trace artifacts are diagnostic-grade for the proven receipt
failure path, but five small precision gaps still slow deeper postmortems:

- sub-millisecond tool calls collapse to `0ms`
- repeated tool-call references do not have a stable call id
- nested tool-call relationships are implicit
- Claude cost is recorded without the token counts that explain it
- MAST mappings exist, but most modes are only directly classified rather than
  exercised through trigger-shaped payloads or event sequences

## Goals

P1. Add microsecond precision to every trace tool call while preserving the
existing integer `duration_ms` field.

P2. Add a stable `tool_call_id` to every trace tool call and preserve it when
the same call is rendered in multiple events.

P3. Add `parent_tool_call_id` or `references_tool_call_id` where the supervisor
can state a deterministic relationship.

P4. Capture Claude token usage (`tokens_in`, `tokens_out`, and component usage)
from live `/lead` JSON output.

P5. Add deterministic trigger coverage for all 14 MAST modes, including the
cross-event modes most likely to drift.

## Non-Goals

- Do not change the truth boundary: receipts and probes still decide workflow
  acceptance.
- Do not infer token counts when Claude does not report them.
- Do not store raw prompts, API keys, or unbounded stdout in tool-call fields.
- Do not require every tool call to have a parent when there is no real parent.

## Acceptance Criteria

- `timed_tool_call` and `ensure_tool_call_timing` emit `duration_us` and
  `tool_call_id`.
- Duplicate rendered references to the same tool call keep the same
  `tool_call_id`.
- Runner post-processing probes reference the Claude invocation via
  `parent_tool_call_id`.
- Claude live and fixture outputs expose token counts in result summaries and
  rendered artifact tables.
- MAST tests prove all 14 modes trigger through either payload classification
  or sequence detection.
