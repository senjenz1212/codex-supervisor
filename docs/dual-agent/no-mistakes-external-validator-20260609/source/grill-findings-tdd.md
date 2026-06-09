# TDD Grill Findings: no-mistakes External Validator

## Findings

### Finding T1

status: resolved
severity: high
question: Do tests prove no-mistakes is external and safe by default?
resolution: Adapter and config tests cover the exact command, skipped shipping
steps, no default `--yes`, missing binary handling, and dependency injection
through a fake subprocess runner.

### Finding T2

status: resolved
severity: high
question: Do workflow tests prove post-acceptance ordering instead of merely
checking helper functions?
resolution: The workflow test runs `run_dual_agent_workflow`, locates the
accepted `outcome_review` event, and asserts the no-mistakes started event is
later in the ledger.

### Finding T3

status: resolved
severity: high
question: Do tests prove no-mistakes cannot rewrite gate authority?
resolution: Required-mode unavailability blocks at `no_mistakes_validation`
while asserting the prior `outcome_review` event remains accepted.

### Finding T4

status: resolved
severity: medium
question: Do tests cover active-worktree isolation?
resolution: The isolated-worktree test initializes a real Git repository, runs
a fake validator in the validation cwd, checks that mutation is detected, and
asserts the active worktree was not changed.

## Decision

No open findings remain. The test plan covers adapter behavior, config safety,
detached workflow preservation, post-acceptance ordering, and stale mutation
guardrails at public boundaries.
