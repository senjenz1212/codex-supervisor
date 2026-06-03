# TDD Grill Findings: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

### Finding 1: Doc assertions need executable anchors

status: resolved

Concern: A documentation-only slice can pass review while naming behaviors
that are not protected by tests.

Resolution: The TDD plan names existing tests for submit, poll, catch-up,
ledger terminal outcomes, migrations, and event replay. The design doc must
quote those test names so the next extraction uses them as the safety net.

### Finding 2: "No source refactor" must be testable

status: resolved

Concern: It is easy to state that no refactor happened while still committing
small helper edits.

Resolution: `test_durable_workflow_job_plan_is_doc_only` is represented by the
git diff check in this run: only docs and task planning artifacts may be
committed.

### Finding 3: Fan-out evidence is gated evidence, not a local test

status: resolved

Concern: The four read-only worker receipts cannot be proven by a local unit
test alone; they are produced by the supervised workflow.

Resolution: The done criteria require the durable workflow transcript and
agentic worker production events to show four read-only workers. The local
tests cover the behavior-pinning inventory; the gate proves fan-out receipts.
