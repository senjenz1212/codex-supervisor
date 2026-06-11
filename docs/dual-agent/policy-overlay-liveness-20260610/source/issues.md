# Issues: Policy Overlay Liveness

## Slice B1: Live Overlay Surface

Priority: P0

Scope: Add one code-owned policy overlay at `.supervisor/policy-overlay.yaml`; read it during gate startup; render it into lead instructions; record a replayable snapshot event; reject non-overlay policy proposal targets.

Acceptance Criteria:
- [ ] `CodexSupervisorMcpAPI._workflow_gate_start_kwargs` includes overlay guidance when the overlay file exists.
- [ ] `supervisor_policy_overlay_snapshot` records overlay path, content hash, block hash, and proposal id.
- [ ] AutoResearch policy proposal creation rejects any target except `.supervisor/policy-overlay.yaml`.
- [ ] Apply and rollback paths reject crafted non-overlay policy targets before writing files or events.

PRD promise: P1, P2.
Public boundary: MCP gate-start and policy-evolution proposal APIs.

## Slice B2: Trend Attribution And Regression Drafts

Priority: P0

Scope: Persist active overlay metadata with quality trend rows and add a regression verifier that drafts a rollback proposal without applying it.

Acceptance Criteria:
- [ ] `record_quality_trends_for_run` stores `policy_overlay_hash` and `policy_proposal_id`.
- [ ] `query_quality_trends` exposes overlay hashes and proposal ids in read-only summaries.
- [ ] Normal workflow result finalization invokes regression verification after trend rows are recorded.
- [ ] Regression verification emits one rollback proposal draft and leaves the overlay file unchanged.

PRD promise: P3, P4.
Public boundary: quality trend recorder/query and workflow-result finalization path.

## Slice B3: Lesson Hygiene And Weekly P11 Audit

Priority: P1

Scope: Fold near-duplicate lessons, track repeated observations, retire no-benefit lessons from injection, and schedule weekly sampled P11 audit observations.

Acceptance Criteria:
- [ ] Repeated near-duplicate lessons produce one row with an increased observation count.
- [ ] Retired lessons remain queryable in history but are excluded from future injection selection.
- [ ] The weekly P11 audit scheduler writes due audit metrics and is idempotent inside the cadence window.

PRD promise: P5.
Public boundary: lesson recording/query helpers and quality trend audit scheduler.

## Coverage

- P1: Slice B1.
- P2: Slice B1.
- P3: Slice B2.
- P4: Slice B2.
- P5: Slice B3.
