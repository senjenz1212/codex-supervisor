# Dual-Agent Branch A+B Hardening Issues

## Slice ISS-1: Failure Taxonomy And Trace Envelope

Type: AFK
Priority: P0
Estimate: M
Scope: Add deterministic failure taxonomy and trace envelope stamping for dual-agent events.
PRD promise: P1, P2
First public-boundary RED test: `test_trace_envelope_stamps_dual_agent_payloads_without_wrapping_original_shape`

Acceptance Criteria:
- [ ] Blocking P11, P12, P_planning, P2, P3, and Cursor failures map to deterministic categories.
- [ ] P4 pause signals are not treated as validation failures.
- [ ] Existing event payload fields remain top-level and backward compatible.

## Slice ISS-2: PRD/TDD Skill Receipt Gate

Type: AFK
Priority: P0
Estimate: M
Scope: Require passing `skill_run` receipts for `to_prd`, `prd_grill`, `to_issues`, `tdd`, and `tdd_grill` before the workflow starts.
PRD promise: P3
First public-boundary RED test: `test_run_dual_agent_workflow_requires_prd_tdd_skill_receipts`

Acceptance Criteria:
- [ ] Missing skill receipts block at `workflow_start`.
- [ ] Valid skill receipts are recorded as `dual_agent_skill_receipt_validation`.
- [ ] `read_gate_transcript` exposes the skill receipt validation result.

## Slice ISS-3: Codex Review Packet

Type: AFK
Priority: P1
Estimate: S
Scope: Emit a structured Codex review packet for each workflow gate decision.
PRD promise: P4
First public-boundary RED test: `test_codex_confidence_report_and_review_packet_explain_decision`

Acceptance Criteria:
- [ ] Gate-round confidence uses `ConfidenceReport.value`.
- [ ] The Codex mailbox message includes requirements, evidence refs, objections, and would-change-if criteria.
- [ ] Exported `interactions.md` renders the review packet.

## Slice ISS-4: Honest Cursor And UI Probe Surfaces

Type: AFK
Priority: P1
Estimate: S
Scope: Add a live Cursor SDK probe script and keep visual evidence gates at the Browser/Computer Use receipt boundary.
PRD promise: P5
First public-boundary RED test: `test_build_cursor_prompt_is_review_only_and_uses_typed_outcome_contract`

Acceptance Criteria:
- [ ] Cursor probe script writes a fixture for both skipped and completed outcomes.
- [ ] The script does not persist API keys.
- [ ] Health and coverage docs distinguish fixture proof from live Cursor proof.
