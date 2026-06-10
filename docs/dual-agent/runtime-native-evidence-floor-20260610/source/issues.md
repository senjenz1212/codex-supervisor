# Runtime-Native Evidence Floor Issues

## Slice 1 - Capture Supervisor Runtime Evidence

Priority: P0

Scope: Add runtime evidence collection for execution and outcome review gates. The collector records baseline git head, captures actual git changed files/name-status, checks deliverable files, reruns declared tests in an isolated validation copy, and returns supervisor-owned receipts plus a ledger payload.

Acceptance Criteria:

- [ ] P1 emits a runtime baseline receipt with `source=supervisor` and `evidence_grade=runtime_native`.
- [ ] P2 reports declared changed files missing from the observed git status/name-status.
- [ ] P3 reports missing, non-file, or empty deliverables.
- [ ] P4 reruns declared tests outside the active worktree and records command results.

## Slice 2 - Enforce The Runtime-Native Floor

Priority: P0

Scope: Wire runtime evidence into `run_dual_agent_workflow` before gate advancement, feed generated receipts into claim and deliverable verification, and block execution or outcome review when the runtime probe is red.

Acceptance Criteria:

- [ ] P5 prevents agent-supplied test or diff receipts from satisfying runtime-sensitive claims.
- [ ] P2 and P3 must be green before execution or outcome review can advance.
- [ ] A passing supervisor test receipt satisfies claimed test-pass evidence.
- [ ] Reviewer decisions cannot turn a red runtime evidence probe into an accepted gate.

## Slice 3 - Persist And Replay Evidence

Priority: P1

Scope: Store runtime evidence as append-only ledger events and expose those events through the existing gate transcript reader.

Acceptance Criteria:

- [ ] P6 reviewer and operator transcripts include `dual_agent_runtime_evidence` events.
- [ ] Event payloads include probe status, receipts, gate, round index, task id, and run id.
- [ ] Existing gate event readers continue to include dynamic workflow, reviewer, skill receipt, and workflow job events.
- [ ] Existing workflow tests remain green with the new runtime evidence event kind.
