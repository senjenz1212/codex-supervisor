# PRD: Dual-Agent Trace Diagnostics

## Problem

Dual-agent and tri-agent runs can now be retraced through `interactions.md`,
`transcript.md`, `transcript.jsonl`, and `replay/manifest.json`, but two
diagnostic dimensions remain too weak for postmortems:

- failure events say the broad supervisor category but do not identify the
  specific MAST failure mode, such as ignored input or premature termination
- tool calls say what happened but do not say when the call started, ended, or
  how long it took

This makes trace review slower than it should be. Operators can see that a gate
blocked, but they still have to manually infer whether the cause was missing
verification, a role violation, repeated workflow steps, ignored objections, or
an execution bottleneck.

## Goals

P1. Failure taxonomy payloads include deterministic MAST mode fields.

P2. The classifier represents all 14 MAST modes through deterministic reason
strings while preserving existing supervisor categories and internal non-MAST
categories.

P3. Replay exports include deterministic cross-event detections and a first
class coverage matrix for the supervisor-verifiable modes: step repetition,
ignored prior input, premature termination, and incorrect verification.

P4. Every `trace_envelope.tool_calls` item has `started_at_ms`,
`ended_at_ms`, and `duration_ms`.

P5. Model and SDK calls record real wall-clock timing where the supervisor owns
the invocation boundary.

P6. Human-readable artifacts expose MAST codes, per-mode coverage, run totals,
and tool timing without requiring the operator to open raw JSON first.

## Non-Goals

- Do not call an LLM to classify failures.
- Do not replace the existing `category`, `subcategory`, or `code` fields.
- Do not implement cryptographic proof for tool receipts.
- Do not make live Claude or Cursor calls part of the default unit test suite.
- Do not change Telegram workflow control.

## PRD Promise Contracts

### P1 - MAST Mode Fields

- User-visible promise: every blocking failure taxonomy includes `mast_code`
  and `mast_mode`, using `null` when the failure is supervisor-specific and not
  covered by MAST.
- Representative action: call `stamp_trace_envelope` on a blocked
  `dual_agent_gate_result` with a failed `P11` probe.
- Public boundary: `dual_agent_slice0`.
- Allowed outcomes: payload preserves existing shape and adds MAST fields.
- Forbidden outcomes: existing callers break because fields were renamed or the
  classifier calls an external model.

### P2 - Full MAST Reason Mapping

- User-visible promise: deterministic `classify_failure` inputs can map to all
  14 MAST modes.
- Representative action: classify reason strings for role violation, step
  repetition, conversation reset, ignored input, premature accept, and false
  green verification.
- Public boundary: `dual_agent_slice0`.
- Allowed outcomes: all 14 codes are reachable through explicit reason
  strings.
- Forbidden outcomes: only current probe IDs map, or multiple modes collapse
  into a generic unknown category.

### P3 - Replay Sequence Detections

- User-visible promise: replay artifacts name cross-event MAST failures that
  cannot be detected from a single event payload alone.
- Representative action: export a run with duplicated gate inputs, ignored
  objections, accepted results with skipped required probes, or Cursor rejecting
  a result that Codex accepted.
- Public boundary: `dual_agent_runner` and artifact export.
- Allowed outcomes: `replay/manifest.json`, `mast-coverage.md`, and
  `replay/mast-coverage.json` contain sequence failure entries, deterministic
  trigger surfaces, event ids, and MAST codes.
- Forbidden outcomes: operators must manually compare events to notice these
  failures.

### P4 - Complete Tool Timing Fields

- User-visible promise: every tool call in every trace envelope has
  `started_at_ms`, `ended_at_ms`, and `duration_ms`.
- Representative action: write a dual-agent event with `metadata.tool_calls`
  and inspect the stamped trace envelope.
- Public boundary: `dual_agent_slice0`.
- Allowed outcomes: missing timing fields are normalized; timed callsites use
  real timings.
- Forbidden outcomes: some tool calls remain timing-free.

### P5 - Invocation Boundary Timing

- User-visible promise: Claude `/lead`, Cursor SDK review, handoff writing,
  planning validation, claim verification, and round recording include
  traceable timing.
- Representative action: run the live failure-mode probe.
- Public boundary: `dual_agent_lead_invocation` and `dual_agent_runner`.
- Allowed outcomes: live trace shows model and SDK tool calls with duration.
- Forbidden outcomes: all durations are synthetic zeroes for real model calls.

### P6 - Human Artifact Projection

- User-visible promise: `interactions.md`, `transcript.md`, `triage.md`,
  `mast-coverage.md`, and `replay/manifest.json` expose the new fields clearly.
- Representative action: export artifacts for a run with MAST failure and tool
  calls.
- Public boundary: artifact export.
- Allowed outcomes: Markdown shows MAST code/mode, timing, missing-receipt
  links, run totals, and per-mode coverage; manifest includes sequence
  detections and points at `replay/mast-coverage.json`.
- Forbidden outcomes: machine JSON has the fields but Markdown hides them.

## Acceptance Criteria

- `classify_failure` returns `mast_code` and `mast_mode`.
- Unit tests cover all 14 MAST mode mappings.
- Existing taxonomy category assertions still pass.
- Artifact export includes run-level sequence failure detections.
- `timed_tool_call` or an equivalent deterministic helper exists and is tested
  with fake clocks.
- All tool calls stamped into `trace_envelope` include timing fields.
- Live failure-mode probe remains runnable and, when Claude/Cursor credentials
  are present, still blocks with `workflow_claim_verification_failed`; skipped
  live reruns are reported as credential-gated rather than claimed as evidence.
- Cursor remains a reviewer/challenger, not the acceptance boundary.

## Risks And Open Questions

- Cross-event ignored-input detection can be noisy if it relies only on text
  matching. The first implementation should be conservative and require
  missing `addresses` plus missing objection text.
- MAST does not cover supervisor-specific governance and execution failures.
  Those must keep `mast_code: null`.
- Some logical tool calls are computed after the fact. They should still carry
  timing fields, but only owned invocation boundaries should claim meaningful
  non-zero duration.
