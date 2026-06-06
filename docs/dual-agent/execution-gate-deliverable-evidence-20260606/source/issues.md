# Issues: Execution Gate Deliverable Evidence

## Slice 1: Add Deterministic Deliverable Evidence Probe

Maps to: P1, P2, P3
Priority: P1

Scope:

- Add a reusable workflow helper that validates accepted `execution` and
  `outcome_review` outcomes.

Acceptance Criteria:

- [ ] Fail when `changed_files` is empty.
- [ ] Fail when changed files are only incidental workflow artifacts.
- [ ] Fail when deliverable files lack a covering diff/implementation receipt.
- [ ] Pin the incidental-only failure with a public workflow-boundary regression test.

## Slice 2: Wire Probe Into Workflow Gate Decisions

Maps to: P1, P2
Priority: P1

Scope:

- Run the probe after a lead outcome is accepted and before reviewer invocation.

Acceptance Criteria:

- [ ] Add the probe to the gate result as `P11`.
- [ ] Let existing Codex review-packet policy force a revision/block on red `P11`.
- [ ] Avoid invoking Cursor when deterministic deliverable evidence already failed.

## Slice 3: Preserve Explicit Report-Only Artifact Work

Maps to: P4
Priority: P2

Scope:

- Allow docs/report-only deliverables only when intent/outcome/receipt text explicitly
  says the task is report-only, docs-only, artifact-only, an ADR, a design doc, or a
  benchmark/report artifact.

Acceptance Criteria:

- [ ] Require the artifact receipt to cover the reported changed files.
- [ ] Block docs-only deliverables on code tasks unless report-only/docs-only scope is explicit.

## Slice 4: Regression Tests

Maps to: P1, P2, P3, P4
Priority: P1

Scope:

- Add workflow-boundary tests for empty changes, missing diff receipts, and explicit
  report-only artifact success.

Acceptance Criteria:

- [ ] Update older claim-verification tests to reflect the stricter execution-stage block
  for missing implementation evidence.
- [ ] Pin outcome-review deliverable failure when claim verification is otherwise green.
