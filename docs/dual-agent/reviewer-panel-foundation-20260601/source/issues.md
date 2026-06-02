# Issues: Reviewer Panel Foundation

## Slice 1: Add Reviewer Registry And Panel Result Shape

Type: AFK
Priority: P0

PRD promises: P1, P2, P4

Scope: Add a thin reviewer-registry interface and map the existing
reviewer result into `independent_reviewer_results[]` without changing the gate
decision. Include mock reviewers for tests and one real Gemini/LiteLLM adapter
through the existing structured reviewer path.

First public-boundary RED test: `run_dual_agent_workflow` with a mock reviewer
runner exposes `independent_reviewer_results[0]` with full provenance while the
legacy `cursor_review` decision remains unchanged.

Acceptance criteria:

- [ ] Registry returns configured reviewers for a gate.
- [ ] Existing single reviewer appears as panel element 0.
- [ ] Panel element includes runtime, model, lineage, tool access, assurance
      grade, transcript refs, hashes, decision, severity, and confidence.
- [ ] Single-reviewer accept/block semantics remain unchanged.

## Slice 2: Emit New Event And Preserve Legacy Replay

Type: AFK
Priority: P0

PRD promises: P3, P4

Scope: Emit `independent_reviewer_review` events alongside legacy
`tri_agent_cursor_review` events and update read/export/replay paths to consume
both. Old fixtures with only the legacy event must still render reviewer
evidence.

First public-boundary RED test: `read_gate_transcript` and exported artifacts
show new panel events for a new run and still show reviewer evidence for a
legacy `tri_agent_cursor_review` fixture.

Acceptance criteria:

- [ ] Event allowlists include `independent_reviewer_review`.
- [ ] Transcript reads include panel reviewer events.
- [ ] Artifacts render panel reviewer results.
- [ ] Legacy `tri_agent_cursor_review` fixtures still pass.

## Slice 3: Document Boundary Migration

Type: AFK
Priority: P1

PRD promises: P5

Scope: Add an ADR explaining the independent reviewer panel boundary,
why legacy Cursor-named keys remain, and when aggregation changes may happen.

First public-boundary RED test: Documentation review in the supervised
outcome-review gate confirms the ADR exists and matches implemented behavior.

Acceptance criteria:

- [ ] ADR records context, decision, compatibility, and consequences.
- [ ] ADR explicitly says aggregation semantics are unchanged.

## Coverage Index

| Promise | Covered by | Status |
|---|---|---|
| P1 panel-schema-exposes-current-reviewer | Issue 1 | Covered |
| P2 registry-produces-reviewers | Issue 1 | Covered |
| P3 new-event-plus-legacy-compatibility | Issue 2 | Covered |
| P4 gate-decision-unchanged | Issues 1, 2 | Covered |
| P5 boundary-decision-is-documented | Issue 3 | Covered |
