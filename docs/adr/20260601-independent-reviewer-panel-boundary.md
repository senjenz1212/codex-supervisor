# ADR: Independent Reviewer Panel Boundary

Date: 2026-06-01

## Status

Accepted

## Context

The supervisor historically exposed the independent reviewer through a single
Cursor-named slot: `cursor_review` plus the `tri_agent_cursor_review` event.
That slot can now be served by Cursor SDK or a lower-assurance structured
LiteLLM/Gemini fallback. The naming is therefore legacy: useful for replay
compatibility, but too narrow for the next phase where the supervisor needs a
panel of reviewers with explicit provenance.

The system is not ready to change verdict aggregation in this slice. Current
single-reviewer gate behavior must remain stable while the schema becomes
panel-shaped.

## Decision

- Add `independent_reviewer_results[]` as the panel-shaped reviewer result
  collection.
- Represent today's single reviewer as element 0 in that collection.
- Emit a new `independent_reviewer_review` event for new consumers.
- Continue writing and reading legacy `tri_agent_cursor_review`, `cursor_review`,
  and `independent_reviewer` payloads.
- Track per-reviewer runtime, model, provider family/lineage, tool access,
  assurance grade, transcript refs, output/transcript hashes, decision,
  severity, and confidence.
- Keep verdict aggregation unchanged. Real reviewer revise/deny still blocks,
  missing verdicts still do not count as accept, and calibrated weighting remains
  a later slice.

## Consequences

New reviewer-panel consumers can adopt `independent_reviewer_review` without an
atomic migration. Existing audit artifacts, replay fixtures, and transcript
readers continue to work against `tri_agent_cursor_review`.

The schema now records enough provenance for future reviewer-panel aggregation
and dependence analysis, but the values are not yet used to change gate
decisions.
