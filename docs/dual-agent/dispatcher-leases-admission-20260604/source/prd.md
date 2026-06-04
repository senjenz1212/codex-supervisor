# PRD: Dispatcher Leases and Admission Control

## Problem Statement

Layer 0 made detached workflow submit a pure durable reservation, but the spawn
transition still needs an always-available recovery driver. Relying on every
caller to poll a specific job is a liveness gap, and retry behavior needs a
single owner so SQLite does not become a multi-claimer queue before the
Postgres Layer 1 work.

## Intent

Add a SQLite-only, single-writer dispatcher that claims reserved and
request-written workflow jobs, leases the claimed work, drives the existing
Layer 0 recovery phases to `spawned`, heartbeats running workers, reaps stale
leases, and applies bounded admission and retry control.

## Solution

Introduce one dispatcher/claimer loop for workflow jobs. The dispatcher claims
eligible `reserved` or `request_written` rows, records `leased_by` and
`lease_expires_at`, writes request files, spawns detached workers, and persists
`recovery_point=spawned` with the worker pid. Detached workers heartbeat their
lease while running. The dispatcher reaper clears stale pre-spawn leases for
redrive and fails spawned jobs whose worker died or lease expired without a
terminal result. Admission control limits concurrent spawned workers, delays
retryable spawn failures with capped exponential backoff and jitter, and parks
poison jobs so they do not loop.

## User Stories

1. As an operator, I want a submitted workflow job to start even if the submit
   caller disconnects and never polls.
2. As a maintainer, I want one SQLite dispatcher to own spawn transitions so
   we do not introduce cross-process claim contention before Layer 1.
3. As an operator, I want stale pre-spawn leases to be reclaimed and retried,
   but malformed jobs to park instead of looping forever.
4. As a budget owner, I want dispatcher admission and budget hooks to prevent
   retry storms and uncontrolled worker fan-out.

## PRD Promise Contracts

P1. Single Dispatcher Claims and Spawns

- User-visible promise: a dispatcher tick claims eligible reserved or
  request-written jobs and advances each claimed job toward `spawned`.
- Representative action: reserve a workflow job, run the dispatcher once.
- Public boundary: `WorkflowJobDispatcher.run_once`.
- Allowed outcomes: request file written, detached worker spawned, pid and
  `recovery_point=spawned` persisted.
- Forbidden outcomes: submit-side spawn, duplicate spawn, or unclaimed side
  effects.
- Related user stories: 1, 2

P2. Leases and Heartbeats Preserve Ownership

- User-visible promise: claimed jobs carry `leased_by` and `lease_expires_at`,
  and the detached worker can extend its lease while running.
- Representative action: claim a job, heartbeat it with the worker lease id.
- Public boundary: `State` workflow-job lease APIs and worker heartbeat helper.
- Allowed outcomes: lease expiry moves forward only for the owner.
- Forbidden outcomes: heartbeats from non-owners extending another worker lease.
- Related user stories: 2

P3. Stale-Lease Reaper Recovers or Fails

- User-visible promise: a stale pre-spawn lease is reclaimed and redriven, and
  a spawned job with a dead or expired worker becomes terminal failure unless a
  terminal result already exists.
- Representative action: seed stale leases, run dispatcher reaper.
- Public boundary: `WorkflowJobDispatcher.reap_stale_leases`.
- Allowed outcomes: reserved/request-written leases clear for redrive; dead or
  expired spawned workers record terminal failure.
- Forbidden outcomes: forever-claimed rows or duplicate spawned workers.
- Related user stories: 1, 3

P4. Admission Control Applies Backpressure

- User-visible promise: the dispatcher does not claim more jobs when the
  configured concurrent spawned-worker cap is full.
- Representative action: seed active leased spawned jobs at capacity, run a
  dispatcher tick.
- Public boundary: `WorkflowJobDispatcher.run_once`.
- Allowed outcomes: no additional job is claimed and a backpressure result is
  returned.
- Forbidden outcomes: claiming beyond capacity or spawning above the cap.
- Related user stories: 4

P5. Retry and Poison Handling Are Bounded

- User-visible promise: retryable spawn failures use one capped backoff layer
  with jitter, while non-retryable malformed jobs park.
- Representative action: force a retryable `Popen` failure and seed a malformed
  request payload.
- Public boundary: `WorkflowJobDispatcher.run_once`.
- Allowed outcomes: retryable rows receive `next_dispatch_at`; poison rows get
  `status=parked` with a reason.
- Forbidden outcomes: tight retry loops or terminal acceptance of a bad job.
- Related user stories: 3, 4

## Implementation Decisions

- Keep SQLite as the only store and use one dispatcher process; no
  `SKIP LOCKED`, no multi-claimer parallelism.
- Add lease and retry columns through forward migrations.
- Keep submit as the Layer 0 pure reservation boundary.
- Route any compatibility poll-side driving through the same dispatcher class
  so the spawn transition has one implementation.
- Keep reviewer panel and gate policy unchanged.

## Testing Decisions

- Start RED tests at dispatcher/state public boundaries, not private helpers.
- Pin admission behavior under saturation before testing happy-path spawn.
- Include heartbeat owner checks so stale leases cannot be extended by a wrong
  worker.
- Include poison parking to prove the retry loop has a non-retryable exit.

## Out of Scope

- Postgres, `FOR UPDATE SKIP LOCKED`, or multi-claimer queues.
- Rewriting submit or changing Layer 0 idempotency semantics.
- Reviewer-panel changes.
