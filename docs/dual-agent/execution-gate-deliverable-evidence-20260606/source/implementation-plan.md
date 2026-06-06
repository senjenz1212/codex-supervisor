# Implementation Plan

## Files / Modules To Touch

- `supervisor/dual_agent_workflow.py`: add the deterministic deliverable-evidence probe and helpers.
- `mcp_tools/codex_supervisor_stdio.py`: wire the probe into accepted execution and outcome-review gate payloads.
- `tests/test_dual_agent_workflow_driver.py`: pin empty-change, incidental-only, docs-only, missing-receipt, explicit report-only, and outcome-review behavior.
- `supervisor/agent_mailbox.py`: keep claim-verification P11 de-duplication from hiding an independent red deliverable-evidence P11.

## Risks

- A too-broad deliverable detector could block legitimate report-only or ADR work, so the allowance must be explicit and receipt-backed.
- A too-narrow incidental-artifact filter could let generated workflow files satisfy execution evidence, making the gate toothless.
- Running the probe after Cursor would waste reviewer calls on deterministically invalid outcomes, so the ordering must stay before reviewer invocation.

## Traceability

- P1, P2, and P3 are covered by `test_execution_gate_blocks_accept_without_deliverable_changes`.
- P3 is covered directly by `test_execution_gate_blocks_accept_with_only_incidental_workflow_files`.
- P2 is covered by `test_execution_gate_blocks_code_change_without_diff_receipt`.
- P4 is covered negatively by `test_execution_gate_blocks_docs_only_change_without_explicit_report_scope`.
- P4 is covered by `test_execution_gate_allows_explicit_report_only_artifact_with_receipt`.
- P1 and P2 at outcome review are covered by `test_outcome_review_blocks_deliverable_failure_even_when_claims_verify`.

## Slice 1: Deliverable Probe

- Add `verify_gate_deliverable_evidence` to `supervisor/dual_agent_workflow.py`.
- Treat `execution` and `outcome_review` as deliverable-required gates.
- Exclude generated workflow artifacts, source planning artifacts, handoff files, and
  scratch files from the deliverable set.
- Require covering receipts for the remaining deliverable files.

## Slice 2: Workflow Wiring

- Import the probe in `mcp_tools/codex_supervisor_stdio.py`.
- Run it before reviewer invocation.
- Store the result in the gate payload as `P11`.
- Skip reviewer invocation when deterministic deliverable evidence is already red.
- Use `deliverable_evidence_failed` as the gate override reason.

## Slice 3: Regression Coverage

- Add public-boundary tests for empty changed files, incidental-only generated artifacts, docs-only scope failure, and missing diff receipts.
- Add a positive explicit report-only artifact test.
- Update outcome-review claim tests for the new earlier execution block.
- Add an outcome-review regression where claim verification is green but deliverable evidence is red.

## Slice 4: Validation

- Run focused workflow tests.
- Run related workflow, live lead, and mailbox tests.
- Run the full suite.
- Run the supervised workflow outcome gate for this slice.
