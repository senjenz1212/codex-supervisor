# PRD: Durable Substrate S2 Idempotent Detached Submit

## Problem Statement

Detached dual-agent workflow submission is currently not retry-safe. A client
that loses the MCP transport after calling `submit_dual_agent_workflow_job`
cannot safely retry, because every submit mints a fresh `workflow-<uuid>` job id
and launches another CLI worker. This creates duplicate runs, duplicate costs,
and confusing operator state. The durable substrate needs submit-level
idempotency before higher-level reconnect flows can become trustworthy.

## Solution

Add an optional `client_token` to detached workflow submit. The supervisor
persists a normalized idempotency token on each detached workflow job and
enforces uniqueness in SQLite. On submit, the supervisor reserves the job by
token before spawning the worker; if the token already exists, the tool returns
the existing job id and current status without launching a second subprocess. If
no token is supplied, the supervisor derives a stable token from `run_id` plus a
canonical hash of the logical request payload.

## User Stories

- As a reconnecting MCP client, I can retry detached submit with the same
  `client_token` and reattach to the original job.
- As a legacy client, I can omit `client_token` and still get retry-safe behavior
  for the same logical submit.
- As an operator, I can trust that a transport drop did not silently launch a
  second detached workflow.
- As a reviewer, I can verify deduplication at the tool boundary without live
  Claude, Cursor, or Telegram services.

## PRD Promise Contracts

P1. explicit-client-token-reattaches

- User-visible promise: A client that retries `submit_dual_agent_workflow_job`
  with the same `client_token` receives the original `job_id` and current job
  status.
- Representative action: Submit the same detached workflow twice with
  `client_token="retry-token"`.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: One row in `dual_agent_workflow_jobs`; one subprocess
  launch; second response returns the first job.
- Forbidden outcomes: A second job row, a second subprocess launch, or a changed
  `job_id` for the same token.

P2. derived-token-covers-legacy-callers

- User-visible promise: Existing callers that omit `client_token` still get a
  job, and retried identical logical submits dedupe using a stable derived key.
- Representative action: Submit the same payload twice without `client_token`.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Existing job id format for the first job; second call
  returns the first job; changed logical payload can create a different job.
- Forbidden outcomes: Requiring callers to send a token, changing the public job
  id format, or deduping unrelated payloads.

P3. atomic-concurrent-submit

- User-visible promise: A race between two identical submits creates exactly one
  job and launches exactly one worker.
- Representative action: Two concurrent calls with the same token.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: One inserted job with a unique idempotency token; all
  callers receive that job.
- Forbidden outcomes: Duplicate rows, duplicate workers, or lock-free
  check-then-insert races.

P4. different-tokens-remain-independent

- User-visible promise: Two distinct client tokens still create distinct jobs.
- Representative action: Submit equivalent payloads with `client_token="a"` and
  `client_token="b"`.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Two job rows and two worker launches.
- Forbidden outcomes: Over-broad deduplication that collapses distinct tokens.

P5. existing-poll-and-replay-semantics-preserve

- User-visible promise: Existing submit/poll flows, `result.json` fallback, job
  transcript export, and deterministic replay continue to work.
- Representative action: Submit a detached job, poll it, and inspect transcript
  workflow job records.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: `poll_dual_agent_workflow_job` behavior unchanged except it
  can report an idempotent reattach; workflow artifacts still export.
- Forbidden outcomes: Gate semantic changes, new live model calls in replay, or
  breaking old result-file polling.

## Implementation Decisions

- Persist `idempotency_token` on `dual_agent_workflow_jobs`.
- Add a unique index for non-null idempotency tokens and migrate existing DBs.
- Reserve a job atomically before `subprocess.Popen`.
- Keep new job ids in the existing `workflow-<hex>` format.
- Return an existing job from submit without editing the existing request,
  result, or log paths.

## Testing Decisions

- Exercise the `supervisor_tool_api` boundary with fake `subprocess.Popen`.
- Assert worker launch count, not just database row count.
- Cover explicit tokens, derived tokens, distinct tokens, and concurrent
  same-token submits.
- Keep the existing `result.json` polling fixture green.
- Cover old database migration in `tests/test_schema_migrations.py`.

## Out Of Scope

- Event append idempotency is deferred to a separate event-log contract.
- Durable terminal outcome storage belongs to S3a.
- Resumable transport protocol belongs to S5.
- Full event-sourcing and projection rebuild belong to S3b.
- Gate sequence, P-probes, and independent-review semantics stay unchanged.
