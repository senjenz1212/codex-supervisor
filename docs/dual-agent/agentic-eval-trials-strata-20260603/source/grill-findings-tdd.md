# TDD Grill Findings: Agentic Eval Trials And Strata

Task id: `agentic-eval-trials-strata-20260603`

### Finding 1: Public boundary must be the runner/report, not helper functions

Status: resolved

The TDD plan names public-boundary tests for `agentic_eval_runner` and
`build_agentic_eval_report`. Helper coverage is listed only after those tests.

### Finding 2: P95 needs both sides of the N gate

Status: resolved

The TDD plan now includes one test for suppression below twenty tasks and one
test for deterministic p95 at twenty or more tasks.

### Finding 3: Class stratification needs an asymmetric fixture

Status: resolved

The TDD plan requires a fixture where one task class wins and another loses, so
pooled summaries cannot satisfy the test accidentally.

### Finding 4: Report-only behavior must remain explicit

Status: resolved

The existing report-only test remains in the plan and guards policy metadata
after trial and class support is added.
