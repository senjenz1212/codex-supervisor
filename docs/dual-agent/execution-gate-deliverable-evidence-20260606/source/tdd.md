# TDD Plan: Execution Gate Deliverable Evidence

## Public Boundary

The first public-boundary tests use `run_dual_agent_workflow`, because the promise is
about whether the supervisor workflow advances or blocks a gate.

## RED/GREEN Cycles

### test_execution_gate_blocks_accept_without_deliverable_changes

Maps to: ISS-1, ISS-2, P1

RED: A fake accepted execution outcome with `changed_files=[]` currently advances the
workflow.

GREEN: Add `verify_gate_deliverable_evidence` and wire it into the gate loop as red
`P11`.

### test_execution_gate_blocks_code_change_without_diff_receipt

Maps to: ISS-1, ISS-2, P2

RED: A fake accepted execution outcome naming a source file but no diff receipt can
advance to outcome review.

GREEN: Require a `git_diff`, `changed_files`, or `implementation` receipt covering all
deliverable files.

### test_execution_gate_blocks_accept_with_only_incidental_workflow_files

Maps to: ISS-1, P3

RED: A fake accepted execution outcome can name only generated workflow files and still
look superficially like it produced artifacts.

GREEN: Exclude generated dual-agent source, transcript, handoff, and scratch paths from
the deliverable set and block with `accepted_gate_only_incidental_files`.

### test_execution_gate_blocks_docs_only_change_without_explicit_report_scope

Maps to: ISS-3, P4

RED: A docs-only changed file with a generic diff receipt can satisfy a code-task
execution gate even when the task never declared report-only or docs-only scope.

GREEN: Require explicit report-only/docs-only/ADR/benchmark language before docs or
report artifacts can be the sole deliverable.

### test_execution_gate_allows_explicit_report_only_artifact_with_receipt

Maps to: ISS-3, P4

RED: A report-only artifact path has no deterministic allowance separate from code
diffs.

GREEN: Detect explicit report-only/docs-only/ADR/benchmark scope and accept a covering
artifact-export receipt.

### Existing Claim Verification Regression Tests

Maps to: ISS-4, P2

RED: Existing outcome-review tests assume missing diff receipts are caught only at
outcome review.

GREEN: Update expectations so missing implementation evidence blocks earlier at
execution, while push/test claim failures still exercise outcome-review P11.

### test_outcome_review_blocks_deliverable_failure_even_when_claims_verify

Maps to: ISS-2, P1, P2

RED: An `outcome_review` response with green claim verification can hide a red
deliverable-evidence P11 probe because claim verification also uses P11.

GREEN: Make red deliverable evidence block independently of claim verification and pin
the outcome-review path with a public workflow-boundary test.

## Verification Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_execution_gate_blocks_accept_without_deliverable_changes tests/test_dual_agent_workflow_driver.py::test_execution_gate_blocks_code_change_without_diff_receipt tests/test_dual_agent_workflow_driver.py::test_execution_gate_allows_explicit_report_only_artifact_with_receipt -q`
- `uv run pytest tests/test_dual_agent_workflow_driver.py tests/test_dual_agent_live_lead_fixture.py tests/test_agent_mailbox.py -q`
- `uv run pytest -q`
