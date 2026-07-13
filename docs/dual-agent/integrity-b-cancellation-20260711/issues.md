# Issues

## Slice ISS-1: Group-Wide Worker Cancellation

Type: AFK
Priority: P0
Estimate: S
Scope: Replace direct `Popen.terminate()` / `Popen.kill()` cleanup with one
dispatcher-contained process-group operation using `SIGTERM`, bounded polling,
`SIGKILL`, direct-child reaping, and a final group-absence assertion.
PRD promise: P1, P3
First public-boundary RED test:
`test_cancel_term_kill_removes_worker_process_group_and_descendant`.

Acceptance Criteria:
- [ ] Cancellation signals the PGID shared by worker and descendant.
- [ ] A `SIGTERM`-resistant worker forces bounded `SIGKILL` escalation.
- [ ] The worker child is no longer addressable through the original PGID.
- [ ] Repeating cleanup after group exit is a no-op.
- [ ] Cleanup refuses to signal the dispatcher process group.

## Slice ISS-2: Stale-Lease Process-Group Cleanup

Type: AFK
Priority: P0
Estimate: S
Scope: Invoke the same process-group termination from the spawned-job stale
failure path before terminal failure is written. Preserve existing result-file,
pre-spawn reclaim, and no-respawn behavior.
PRD promise: P2, P3
First public-boundary RED test:
`test_stale_lease_kills_process_group_before_marking_job_failed`.

Acceptance Criteria:
- [ ] An expired spawned lease kills its live process group.
- [ ] State completion observes an already-empty group.
- [ ] The job ends in failed terminal state with the existing stale-worker error.
- [ ] A persisted dead PID remains an idempotent no-op.
- [ ] Existing dispatcher lease and spawn tests remain green.

## Slice ISS-3: Descendant Evidence and Regression Receipts

Type: AFK
Priority: P1
Estimate: S
Scope: Record deterministic RED/GREEN evidence, retain the real descendant PID
proof, run focused dispatcher tests, and document any platform limitation.
PRD promise: P1, P2, P3
First public-boundary RED test:
`test_terminating_already_dead_process_group_is_idempotent`.

Acceptance Criteria:
- [ ] The focused test file proves parent and child initially share one PGID.
- [ ] The cancellation and stale paths each leave that PGID empty.
- [ ] The already-dead group test completes twice without error.
- [ ] Python compilation and `git diff --check` pass.
