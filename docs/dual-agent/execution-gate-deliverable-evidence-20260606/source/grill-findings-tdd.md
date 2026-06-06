# TDD Grill Findings

## Finding 1: The Test Must Hit The Workflow Boundary

Helper-only tests would not prove that the gate loop blocks advancement.

Resolution: the first three tests invoke `run_dual_agent_workflow`.

## Finding 2: The Report-Only Escape Hatch Needs A Positive Case

Without a positive report-only case, the fix could accidentally block existing ADR and
benchmark-report workflows.

Resolution: add a report-only artifact test with a covering `artifact_export` receipt.

## Finding 3: Existing P11 Tests Should Keep Their Intent

The stricter execution gate changes where missing diff evidence is caught. Existing
claim-verification tests should still cover outcome-only claims such as remote push and
test receipts.

Resolution: update receipt setup so push/test claim failures reach `outcome_review`,
while stale/missing implementation receipts are expected to block at `execution`.
