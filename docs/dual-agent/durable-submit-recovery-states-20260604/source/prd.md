# PRD: Durable Submit Recovery States

## Problem Statement

Detached dual-agent workflow submission still couples a transport-facing MCP call
to request-file writing and `subprocess.Popen`. If the process or transport dies
between durable reservation and worker spawn, the job can sit at `submitted` with
`pid=NULL` forever while retries blindly reattach. Idempotency is also not
expressed as the active-job database invariant the future multi-writer store
will need.

## Intent

Make `submit_dual_agent_workflow_job` a pure durable reservation with explicit recovery phases and database-enforced idempotency on SQLite. A dropped MCP submit must not strand a job at `submitted` with no worker, and duplicate submit attempts must reattach to the same durable job rather than relying on process-local serialization.

## Solution

Persist enough information on the workflow job row to move all side effects out
of submit. Submit reserves a row at `recovery_point=reserved` and returns a
`job_id`. Poll becomes the outside-submit recovery driver: it writes the request
file from durable payload JSON, records `request_written`, spawns the detached
CLI worker, records `spawned`, and terminal completion records `terminal`.
SQLite enforces one active job per idempotency token with a partial unique
index. Terminal rows replay stored outcomes through the reservation API.

## User Stories

1. As an operator retrying after an MCP transport drop, I want the same
   `client_token` to reattach to the durable job so that retries do not create
   duplicate active workers.
2. As a workflow caller, I want submit to return after durable reservation so
   that a slow or failing detached CLI spawn cannot close the submit transport.
3. As a maintainer, I want explicit recovery phases so that a reserved or
   request-written job can be resumed or failed deterministically.
4. As a reviewer, I want tests that prove process-race behavior and stranded
   reservation recovery at the public boundary.

## PRD Promise Contracts

P1. Submit Is Pure Reservation

- User-visible promise: submitting a detached dual-agent workflow durably reserves a job and returns a `job_id` without writing request files or spawning the detached CLI worker in the submit call.
- Representative action: call `submit_dual_agent_workflow_job` with a stable `client_token`.
- Public boundary: MCP tool `submit_dual_agent_workflow_job`.
- Allowed outcomes: returns `status=submitted`, `recovery_point=reserved`, `poll_tool=poll_dual_agent_workflow_job`, and stable paths.
- Forbidden outcomes: `subprocess.Popen` during submit, request-file writes during submit, or missing `job_id`.
- Related user stories: 2

P2. Idempotency Is Enforced By SQLite

- User-visible promise: repeated submits for the same idempotency token resolve to exactly one non-terminal workflow job even across process races.
- Representative action: run eight concurrent reservations with one token.
- Public boundary: `State.reserve_dual_agent_workflow_job` and the SQLite schema.
- Allowed outcomes: one created row, all others reattach; direct duplicate active insert fails by unique constraint.
- Forbidden outcomes: duplicate active jobs for one token, or dedupe depending only on Python locks.
- Related user stories: 1, 4

P3. Recovery Phases Are Durable

- User-visible promise: detached jobs expose a durable recovery state machine: `reserved -> request_written -> spawned -> terminal`.
- Representative action: poll a reserved job and observe request write and spawn transitions.
- Public boundary: `poll_dual_agent_workflow_job` and `dual_agent_workflow_jobs`.
- Allowed outcomes: each phase is recorded on the job row before the next effect.
- Forbidden outcomes: phase-less `submitted` rows that cannot be resumed.
- Related user stories: 3

P4. Reattach Resumes Or Replays

- User-visible promise: retrying or polling an existing job resumes from the last durable phase, and terminal jobs replay the stored terminal outcome.
- Representative action: re-submit a reserved/request-written/terminal job with the same token, then poll.
- Public boundary: `submit_dual_agent_workflow_job` and `poll_dual_agent_workflow_job`.
- Allowed outcomes: active jobs reattach with recovery metadata; polling re-drives reserved/request-written jobs; terminal jobs return stored results.
- Forbidden outcomes: blind reattach to a stranded reservation or double-spawn.
- Related user stories: 1, 3

P5. Stranded Reservations Are Recovered

- User-visible promise: a job stuck before `Popen` with `pid=NULL` is re-driven or failed; it is never left forever at `submitted`.
- Representative action: create a reserved job with no worker, then poll it.
- Public boundary: `poll_dual_agent_workflow_job`.
- Allowed outcomes: poll writes the request and spawns the worker, or records a terminal failure if spawning fails.
- Forbidden outcomes: infinite `submitted` with no pid and no terminal result.
- Related user stories: 3, 4

## Implementation Decisions

- Keep this slice SQLite-only and use a forward migration for new columns and
  the active idempotency index.
- Store canonical request payload JSON on the job row so submit does not need to
  write a request file.
- Use `poll_dual_agent_workflow_job` as the Layer 0 outside-submit recovery
  driver; a separate dispatcher with leases remains Layer 0.5.
- Preserve terminal-result ledger behavior and existing `catch_up` cursor
  semantics.

## Testing Decisions

- Start RED tests at the MCP submit and poll tool boundaries, not only helper
  functions.
- Include a multiprocessing race against `State.reserve_dual_agent_workflow_job`
  to avoid proving only thread-local locking.
- Add direct SQLite duplicate-active-token coverage for the declarative
  constraint.
- Keep full-suite regression coverage to prove unrelated gate and reviewer
  behavior remains green.

## Out of Scope

- No Postgres, `SKIP LOCKED`, multi-claimer queue, or lease dispatcher.
- No admission-control redesign.
- No reviewer-panel changes.
- No weakening of P1/P2/P3/P13/P14 gates.
