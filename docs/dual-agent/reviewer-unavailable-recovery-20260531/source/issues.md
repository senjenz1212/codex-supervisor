# Reviewer Unavailable Recovery Issues

## Slice ISS-1: Policy Surface

Type: AFK
Priority: P0
Estimate: S
Scope: Add `reviewer_unavailable_policy` to config, MCP workflow parameters,
submit-job payloads, and CLI workflow payload filtering.
PRD promise: P1, P4
First public-boundary RED test: CLI payload preservation plus workflow default
policy assertion.

Acceptance Criteria:
- [ ] Config default is `escalate`.
- [ ] `run_dual_agent_workflow` accepts explicit `block`, `escalate`, and
  `proceed_degraded`.
- [ ] Workflow CLI `WORKFLOW_KEYS` preserves the new field.
- [ ] `lead_direct` default is unchanged.

## Slice ISS-2: Degraded Recovery Receipt

Type: AFK
Priority: P0
Estimate: M
Scope: Add supervisor-derived reviewer-unavailable recovery evidence when
Cursor returns `reviewer_contract_unmet` or
`reviewer_infrastructure_unavailable`.
PRD promise: P2, P3
First public-boundary RED test: `run_dual_agent_workflow` with a Cursor
contract-miss fixture and `proceed_degraded`.

Acceptance Criteria:
- [ ] `proceed_degraded` advances on Claude accept plus Codex accept.
- [ ] Missing Cursor verdict is recorded as degraded evidence.
- [ ] Missing Cursor verdict is never counted as accept.
- [ ] Transcript reads expose the recovery receipt.

## Slice ISS-3: Resumable Human Escalation

Type: AFK
Priority: P0
Estimate: M
Scope: Make default `escalate` create a resumable human action using the
existing resume-signal machinery, then allow a human `Continue` to proceed
degraded.
PRD promise: P3
First public-boundary RED test: first workflow run pauses; a resume signal then
allows the rerun to advance.

Acceptance Criteria:
- [ ] Default policy creates a paused human action.
- [ ] The first result explains reviewer infrastructure unavailability.
- [ ] A continue resume is claimed once.
- [ ] The rerun records authorized degraded recovery and advances.

## Slice ISS-4: Safety Regression Rails

Type: AFK
Priority: P0
Estimate: M
Scope: Preserve real Cursor rejection blocking and prevent auto-degraded
proceed for high-stakes evidence paths.
PRD promise: P5, P6
First public-boundary RED test: valid Cursor `revise` still blocks; agentic or
runtime-native evidence escalates despite requested `proceed_degraded`.

Acceptance Criteria:
- [ ] Valid Cursor `revise` and `deny` still block.
- [ ] `agentic_lead_policy=required` escalates on reviewer unavailable.
- [ ] `required_evidence_grade=runtime_native` escalates on reviewer
  unavailable.
- [ ] No P1/P2/P3/P13/P14 bypass is introduced.
