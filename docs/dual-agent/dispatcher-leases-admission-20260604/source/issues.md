# Issues

## Slice ISS-1: Lease Schema and State APIs

Type: AFK
Priority: P0
Estimate: M
Scope: Add workflow-job lease, heartbeat, retry, and park fields; expose state
helpers to claim one dispatchable job, extend a lease, clear stale pre-spawn
leases, and park poison jobs.
PRD promise: P2, P3, P5
First public-boundary RED test: state lease claim and heartbeat tests against
SQLite.

Acceptance Criteria:
- [ ] Claimed jobs persist `leased_by` and `lease_expires_at`.
- [ ] Heartbeat extends only when `leased_by` matches.
- [ ] Stale reserved/request-written leases can be reclaimed.
- [ ] Poison rows park with a stable reason and are no longer dispatchable.

## Slice ISS-2: Single Dispatcher Spawn Driver

Type: AFK
Priority: P0
Estimate: M
Scope: Add a single dispatcher implementation that claims eligible jobs and
drives `reserved -> request_written -> spawned` using the existing detached CLI
command.
PRD promise: P1, P3
First public-boundary RED test: `WorkflowJobDispatcher.run_once` on a reserved
job with `subprocess.Popen` faked.

Acceptance Criteria:
- [ ] A dispatcher tick writes the request before spawning.
- [ ] Spawn persists pid and `recovery_point=spawned`.
- [ ] Request-written jobs spawn without rewriting duplicate request state.
- [ ] Spawned stale/dead workers fail rather than respawn.

## Slice ISS-3: Admission, Backoff, and Poison Parking

Type: AFK
Priority: P0
Estimate: M
Scope: Enforce max concurrent spawned workers, a budget hook, capped exponential
backoff with jitter for retryable failures, and parking for non-retryable jobs.
PRD promise: P4, P5
First public-boundary RED test: dispatcher run under a saturated active-worker
cap.

Acceptance Criteria:
- [ ] At capacity, dispatcher claims no new job.
- [ ] Retryable spawn failure sets `next_dispatch_at`.
- [ ] Repeated retryable failures park at the configured attempt cap.
- [ ] Malformed request payload parks immediately.

## Slice ISS-4: Worker Heartbeat and CLI Surface

Type: AFK
Priority: P1
Estimate: S
Scope: Add a dispatcher CLI/loop and make detached workflow workers heartbeat
their lease while running.
PRD promise: P2
First public-boundary RED test: heartbeat helper extends the lease and rejects
wrong-owner heartbeats.

Acceptance Criteria:
- [ ] Dispatcher loop can run as a long-lived process.
- [ ] Worker heartbeat thread extends the lease for the spawned job.
- [ ] Existing durable result persistence remains unchanged.
- [ ] Focused tests and full suite pass.
