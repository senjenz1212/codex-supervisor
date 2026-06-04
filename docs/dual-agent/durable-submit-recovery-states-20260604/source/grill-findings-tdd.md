# TDD Grill Findings

## Findings

### Finding T1

status: resolved
severity: high
question: Do tests prove the public submit boundary rather than helpers only?
resolution: `test_submit_dual_agent_workflow_job_reserves_then_poll_spawns_worker`
patches `subprocess.Popen` through the MCP tool boundary, asserts no request
file exists after submit, and asserts the first poll persists
`request_written` before spawning.

### Finding T2

status: resolved
severity: high
question: Does concurrency coverage escape the process-local `_write_lock`?
resolution: `test_reserve_dual_agent_workflow_job_eight_process_race_reserves_once`
uses multiprocessing against the shared SQLite database and checks one row.

### Finding T3

status: resolved
severity: medium
question: Do recovery tests prove side-effect ordering?
resolution: Poll tests assert `reserved`, observe `request_written` inside the
spawn boundary, then assert `spawned` and `terminal` recovery points around
request writing, pid update, and failure.

### Finding T3a

status: resolved
severity: high
question: Could a crash or persist failure after `Popen` but before spawned-state
persistence produce a second worker?
resolution: `test_poll_dual_agent_workflow_job_kills_worker_when_spawn_persist_fails`
and `test_poll_dual_agent_workflow_job_stale_spawn_claim_fails_without_respawn`
cover the post-`Popen`/pre-persist window. The implementation terminates a
just-created worker if spawned-state persistence fails and treats stale
`spawn:*` claims as terminal ambiguity rather than re-driving a second spawn.

### Finding T4

status: resolved
severity: medium
question: Does terminal replay avoid live effects?
resolution: Terminal replay coverage re-submits with the same token and checks
the stored outcome is returned without requiring a worker result file.

### Finding T5

status: resolved
severity: medium
question: Could existing durable-result behavior regress silently?
resolution: Existing durable result, ledger-wins-over-cache, catch-up, and full
workflow driver tests remain in the focused and full-suite runs.

## Decision

All TDD grill findings are resolved. No waived or open findings remain.
