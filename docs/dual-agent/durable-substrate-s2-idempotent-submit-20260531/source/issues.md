# Issues: Durable Substrate S2

## Slice 1: Persist Job Idempotency Tokens

Priority: P0

Scope: Add an optional `client_token` to detached submit, persist a normalized
idempotency token on `dual_agent_workflow_jobs`, and add a unique index with an
old-database migration.

Acceptance Criteria:

- [ ] Same `client_token` submitted twice returns one `job_id`.
- [ ] The second submit does not call the worker launcher.
- [ ] The job row records the idempotency token.

PRD promise: P1, P3

## Slice 2: Derive Stable Keys For Existing Callers

Priority: P0

Scope: Canonicalize the detached request payload and derive a stable token from
`run_id` plus request hash when `client_token` is absent.

Acceptance Criteria:

- [ ] Two identical no-token submits return the first job.
- [ ] New jobs keep the `workflow-<hex>` job id shape.
- [ ] Different logical payloads can still create distinct jobs.

PRD promise: P2, P5

## Slice 3: Prove Atomic Concurrent Submit

Priority: P1

Scope: Reserve the job atomically using SQLite transaction and unique-token
semantics before spawning the subprocess.

Acceptance Criteria:

- [ ] Concurrent same-token submit returns exactly one unique job id.
- [ ] Concurrent same-token submit makes exactly one fake `Popen` call.
- [ ] Database contains one row for the token.

PRD promise: P3

## Slice 4: Preserve Distinct Tokens And Poll Compatibility

Priority: P1

Scope: Ensure different tokens create different jobs and the existing poll path
still reads result-file based jobs.

Acceptance Criteria:

- [ ] Different explicit tokens launch different jobs.
- [ ] Existing submit test still passes.
- [ ] Existing poll/result-file fixture still passes.

PRD promise: P4, P5

## Slice 5: Record Deferred Event Append Idempotency

Priority: P2

Scope: Document that `(run_id, idempotency_key)` event append deduplication is a
separate event-log issue and not part of this submit-dedup slice.

Acceptance Criteria:

- [ ] PRD and grill findings name append idempotency as deferred.
- [ ] Implementation does not alter event append semantics.

PRD promise: P5
