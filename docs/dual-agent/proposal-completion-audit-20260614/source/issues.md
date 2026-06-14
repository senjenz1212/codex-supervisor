# Issues: Proposal Completion Audit

## Slice 1: Rebuild Planning And Pass The Planning Gate

Priority: P0

Scope: Replace the generated source artifact stubs with real PRD, grill, issue, TDD, and implementation-plan artifacts for `proposal-completion-audit-20260614`.

PRD promise: P4

Acceptance Criteria:

- [ ] `source/prd.md` includes problem statement, solution, user stories, PRD promise contracts, implementation decisions, testing decisions, and out of scope.
- [ ] `source/issues.md` includes independently checkable slices with PRD promise mapping.
- [ ] `source/tdd.md` includes concrete test names with RED/GREEN steps and mappings.
- [ ] `source/implementation-plan.md` includes files or modules to touch, risks, and traceability.
- [ ] `validate_planning_artifacts` passes for all route gates.

## Slice 2: Run The Read-Only Proposal Completion Audit

Priority: P0

Scope: Execute the supervised workflow through the durable detached worker and produce an outcome report grounded in production code, daemon wiring, AXI state, and ledger events.

PRD promise: P1, P2, P3, P4

Acceptance Criteria:

- [ ] The audit classifies proposal areas as implemented, partial, missing, or live-unproven.
- [ ] The audit distinguishes automatic daemon/schedule behavior from manual CLI-only behavior.
- [ ] The audit records `doctor`, `experiments list`, `trends`, source inspection, and git status evidence.
- [ ] The audit confirms `.supervisor/policy-overlay.yaml` and proposal statuses were not mutated by the run.
- [ ] The workflow reaches terminal accepted status or reports the exact remaining gate blocker.

## Slice 3: Report Remaining Operator Decisions

Priority: P1

Scope: Summarize what remains after the audit without taking the operator actions inside the audit.

PRD promise: P2, P3

Acceptance Criteria:

- [ ] The report identifies whether any AutoResearch drafts or runnable experiments remain.
- [ ] The report identifies whether pending proposals or rollback drafts remain.
- [ ] The report identifies whether D1 or D2 is supported by normalized trend data.
- [ ] The report recommends the next safe operator action without activating, applying, or approving anything.
