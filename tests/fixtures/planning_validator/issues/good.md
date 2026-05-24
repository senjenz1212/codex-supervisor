# Good Issues Fixture

## Slice ISS-1: Pure Validator

Type: AFK
Priority: P0
Estimate: M
Scope: Add deterministic per-kind validation for PRD, issues, TDD, grill, and
implementation plan artifacts.
PRD promise: P1, P2
First public-boundary RED test: `dual_agent_planning_validator` fixture matrix.

Acceptance Criteria:
- [ ] Good fixture passes with stable check IDs.
- [ ] Stub fixture fails before worker invocation.
- [ ] Sneaky fixture fails even when headings are present.

## Slice ISS-2: Runner Receipt

Type: AFK
Priority: P1
Estimate: S
Scope: Wire the validator into `run_dual_agent_gate` and persist the receipt to
the supervisor event ledger.
PRD promise: P1, P3
First public-boundary RED test: `dual_agent_runner` blocks before fake runner call.

Acceptance Criteria:
- [ ] The worker is not invoked when planning validation fails.
- [ ] Accepted gates include a green planning probe.
- [ ] `read_gate_transcript` includes planning-validation receipts.
