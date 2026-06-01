# TDD Grill Findings

## Findings

### Finding TG1: First Tests Hit The Public Tool Boundary

- Check: RED 1 through RED 4 call `submit_dual_agent_workflow_job` instead of
  testing a helper directly.
Status: resolved

### Finding TG2: Race Test Must Assert Launch Count, Not Only Row Count

- Check: Duplicate prevention is only useful if the second call does not spawn a
  worker.
Status: resolved

### Finding TG3: Old-DB Compatibility Needs Migration Coverage

- Check: Existing state DBs will not gain new columns from `CREATE TABLE IF NOT
  EXISTS`.
Status: resolved

### Finding TG4: Append Idempotency Is Explicitly Deferred

- Check: TDD does not pretend event append idempotency is covered.
Status: resolved
