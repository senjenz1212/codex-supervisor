# PRD: Reviewer Panel Foundation

## Problem Statement

The supervisor has one independent-reviewer slot even though the runtime can
route that slot through Cursor SDK or a Gemini/LiteLLM fallback. The exported
shape still says `cursor_review` and emits `tri_agent_cursor_review`, so future
multi-reviewer work would either overload a legacy name or break old replay
consumers. Operators need a truthful panel-shaped schema before aggregation,
weighting, or second-vendor reviewers are introduced.

This slice converts the single slot into a provenance-preserving reviewer panel
schema without changing gate decisions. Today's single reviewer becomes element
0 in `independent_reviewer_results[]`. The existing `cursor_review`,
`independent_reviewer`, and `tri_agent_cursor_review` compatibility surfaces
remain readable and writable.

## Solution

Add a thin reviewer-registry boundary that returns the reviewers configured for a
gate. Keep the implementation deliberately small: one real reviewer adapter for
the existing Gemini/LiteLLM structured reviewer path, plus mock reviewers for
tests. The registry exposes panel results with per-reviewer provenance:
reviewer id, runtime, model, provider lineage, tool access, assurance grade,
transcript/output refs and hashes, decision, severity, and confidence.

Write a new `independent_reviewer_review` ledger event containing
`independent_reviewer_results[]`, while continuing to write the legacy
`tri_agent_cursor_review` event and single-slot payloads. Read/export/replay
paths must understand both event kinds. Gate decision semantics remain exactly
equivalent to current main for a single configured reviewer.

## User Stories

1. As a supervisor operator, I want reviewer traces to say which reviewer
   actually ran, so that Cursor, Gemini, and future reviewers are not conflated.
2. As an auditor, I want every reviewer result to carry runtime, model, lineage,
   assurance, transcript refs, hashes, decision, severity, and confidence.
3. As a maintainer, I want old `tri_agent_cursor_review` events to keep reading
   and exporting while new consumers adopt `independent_reviewer_review`.
4. As a workflow owner, I want the current single-reviewer gate decision to stay
   unchanged while the schema changes underneath it.
5. As a future panel author, I want a small registry interface with mock
   reviewers for tests and the current Gemini/LiteLLM reviewer as the real
   adapter.

## PRD Promise Contracts

P1. panel-schema-exposes-current-reviewer

- User-visible promise: A workflow with one configured independent reviewer
  exposes `independent_reviewer_results[]`, with today's reviewer as element 0.
- Representative prompts or actions: Run `run_dual_agent_workflow` with cursor
  review enabled and inspect the gate result and reviewer events.
- Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
- Allowed outcomes: `independent_reviewer_results[0]` contains reviewer id,
  runtime, model, provider family or lineage, tool access, assurance grade,
  transcript refs, output/transcript hashes, decision, severity, and confidence.
- Forbidden outcomes: only a legacy `cursor_review` payload, missing provenance,
  unhashable transcript/output evidence, or changed gate decision semantics.

P2. registry-produces-reviewers

- User-visible promise: The supervisor can obtain reviewers through a thin
  registry interface instead of hard-coding a single reviewer slot.
- Representative prompts or actions: Ask the registry for reviewers for a gate
  in tests and execute mock reviewers plus the real Gemini/LiteLLM adapter.
- Public boundary: reviewer-registry module API plus workflow integration.
- Allowed outcomes: registry returns deterministic 1-3 reviewer specs/results,
  supports mocks, and routes the real structured Gemini reviewer through the
  existing typed outcome path.
- Forbidden outcomes: a broad plugin framework, hard-coded second vendor, or
  bypassing typed outcome validation.

P3. new-event-plus-legacy-compatibility

- User-visible promise: New runs emit `independent_reviewer_review`, and old
  `tri_agent_cursor_review` events still read/export/replay.
- Representative prompts or actions: Run a workflow, read gate transcript,
  export artifacts, and replay an old fixture containing only
  `tri_agent_cursor_review`.
- Public boundary: `read_gate_transcript`, `export_gate_artifacts`, and event
  read allowlists.
- Allowed outcomes: new event contains the panel results; legacy event remains
  present and readable; exporters render both without duplicate semantic loss.
- Forbidden outcomes: old fixtures disappear, replay drops reviewer evidence, or
  consumers must migrate atomically.

P4. gate-decision-unchanged

- User-visible promise: This schema/API foundation does not change whether a
  representative gate accepts or blocks.
- Representative prompts or actions: Compare a single-reviewer accept and a
  single-reviewer revise/deny run before and after the panel schema.
- Public boundary: `run_dual_agent_workflow`
- Allowed outcomes: existing single-reviewer AND behavior remains equivalent;
  missing verdicts do not count as accept; real revise/deny still blocks.
- Forbidden outcomes: weighted aggregation, extra auto-accept paths, degraded
  recovery over real reviewer objections, or changed default reviewer behavior.

P5. boundary-decision-is-documented

- User-visible promise: Future maintainers can understand why the system has a
  new panel event while retaining legacy Cursor-named aliases.
- Representative prompts or actions: Read the ADR for the reviewer panel
  boundary migration.
- Public boundary: `docs/adr`
- Allowed outcomes: ADR explains context, decision, compatibility, and
  consequences.
- Forbidden outcomes: undocumented rename, removal of legacy keys, or hidden
  change in gate algebra.

## Implementation Decisions

- Add a small reviewer-registry module rather than a generic plugin framework.
- Represent panel output as `independent_reviewer_results[]` on reviewer events
  and gate payloads.
- Map the existing single `CursorInvocationResult` into a panel element with
  stable provenance fields.
- Keep `cursor_review` and `independent_reviewer` as compatibility aliases.
- Emit `independent_reviewer_review` in addition to `tri_agent_cursor_review`.
- Update read/export/replay allowlists and artifact rendering for the new event.
- Add an ADR for the panel boundary and compatibility migration.
- Do not change `codex_decision`, `cursor_decision`, reviewer-unavailable
  recovery, or `reviewer_output_mode` defaults in this slice.

## Testing Decisions

- First RED tests must hit public workflow/transcript/export boundaries rather
  than only helper mapping functions.
- Mock reviewers prove the registry can return multiple reviewers without
  requiring a second vendor route.
- The real Gemini/LiteLLM reviewer path is exercised with a deterministic fake
  OpenAI client through the existing structured reviewer adapter.
- Old fixture replay covers legacy `tri_agent_cursor_review` compatibility.
- Representative gate-decision regression compares accept/block behavior with
  the current single-reviewer path.
- Full suite must remain green.

## Out Of Scope

- Changing verdict aggregation or AND semantics.
- Adding calibrated weights, dependence scoring, or confidence routing.
- Adding a second vendor reviewer.
- Removing legacy `cursor_review` or `tri_agent_cursor_review`.
- Fixing Cursor SDK's external `internal: internal error`.
