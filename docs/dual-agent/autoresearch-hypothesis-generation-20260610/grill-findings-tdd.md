# TDD Grill Findings

## Finding 1: Tests Must Prove No Self-Run

- Risk: A generator test that only inspects the draft payload would miss accidental execution.
- Resolution: The draft test must call the runner before activation and assert no report and no workflow job row.

## Finding 2: Durable Execution Must Be Observable

- Risk: The runner could call the evaluator directly and still produce a report.
- Resolution: The runnable test must assert `dual_agent_workflow_jobs` contains the AutoResearch evaluator job.

## Finding 3: Immutable Report-Only Needs A Queue Row

- Risk: A skipped immutable signal gives no operator visibility.
- Resolution: The immutable test must assert `status=report_only` and a proposal pointer payload.

## Finding 4: Weekly Cap Needs Stable Time

- Risk: Wall-clock tests will flake around week boundaries.
- Resolution: Pass `now` into the runner in tests.
