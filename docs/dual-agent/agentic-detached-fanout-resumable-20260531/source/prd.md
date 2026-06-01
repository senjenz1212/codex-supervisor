# PRD: Agentic Detached Fan-Out Resume Safety

## Problem Statement

Agentic fan-out can now be produced by the supervisor-owned executor, but the
detached job path has not been proven safe across transport drops, retries, or
worker crashes. Detached submit already reattaches by idempotency token, and the
CLI invokes the same `run_dual_agent_workflow` boundary as inline runs. The
missing contract is durable fan-out continuity: a resumed or restarted workflow
must discover already completed worker receipts before planning new workers, and
must clean up stale worker processes from dead attempts.

## Blocking Open Question Resolution

Question: when a detached agentic job is reattached or restarted, are prior
worker receipts hydrated from durable storage before
`produce_agentic_worker_receipts` runs?

Resolution: no. The current detached submit path writes a request payload and
the CLI calls `run_dual_agent_workflow`; that workflow builds
`receipt_payloads` only from the caller's `tool_receipts` before calling
`produce_agentic_worker_receipts`. It does not read prior
`dual_agent_agentic_worker_production` events or `.handoff/agentic-workers`
receipt files. The producer can skip when `existing_receipts` already contains
subagent receipts, but no durable hydration feeds that skip path on resume.

## Solution

Add a durable agentic worker receipt hydration and cleanup layer before the
agentic producer runs. The workflow should merge caller receipts, prior ledger
worker production receipts, and replay-verifiable `.handoff/agentic-workers`
receipts. The producer should reuse completed workers and spawn only missing or
previously failed roles. Detached retries should continue to use S2
idempotency; S3a ledger terminal outcomes and S5 catch-up should expose the
worker production/progress events needed by reconnecting clients.

## Implementation Decisions

- Keep `lead_direct` and `agentic_lead.policy=off` as the default execution
  path; resumable fan-out behavior only changes runs that explicitly request
  agentic worker receipts through existing policy knobs.
- Add hydration inside the supervisor-owned workflow boundary before
  `produce_agentic_worker_receipts` so P13 receives caller receipts plus durable
  ledger and `.handoff/agentic-workers/<task>/` receipts in one replayable list.
- Treat only passing replay-verifiable `dynamic_subagent_result` receipts as
  completed work; failed, timeout, or hash-mismatched receipts remain audit
  evidence and must not satisfy required roles or minimum subagent counts.
- Keep S2 idempotent submit, S3a ledger terminal outcome, S5 catch-up, P13/P14,
  and Cursor review as the authority layers rather than replacing them with a
  new fan-out-specific control plane.
- Persist enough worker runtime metadata to support task-level cleanup and emit
  progress events for catch-up, while leaving the terminal receipt as the
  evidence authority for policy enforcement.

## Testing Decisions

- Start with public-boundary tests that invoke `run_dual_agent_workflow`,
  `submit_dual_agent_workflow_job`, `catch_up_dual_agent_workflow`, and
  `poll_dual_agent_workflow_job` through the supervisor tool API boundary.
- Prove resume correctness with durable fixtures that include ledger events,
  `.handoff/agentic-workers/<task>/` refs, non-duplicated idempotent submits,
  and terminal outcomes that survive deletion of legacy `result.json`.
- Add helper tests for receipt discovery, role-aware partial fan-out, worker
  runtime cleanup, and progress callback emission only after the public
  workflow-boundary regression exists.
- Keep regression coverage for required-policy fail-closed behavior,
  runtime-native hash verification, default-off fan-out, and downstream Cursor
  review so reliability work does not weaken quality gates.

## User Stories

- As a reconnecting MCP client, I can retry detached submit with the same
  `client_token` and reattach to the original job without launching duplicate
  workers.
- As an operator, I can restart a workflow after a crash and trust completed
  worker receipts will be reused instead of repeated.
- As a supervisor reviewer, I can replay worker transcript/output hashes from
  durable `.handoff` refs and verify `runtime_native` evidence through P13.
- As an operator recovering a dead detached job, stale worker processes from a
  prior attempt are reaped before resume work starts.
- As a client using catch-up, I can see worker production/progress events that
  occurred during a disconnected window.

## PRD Promise Contracts

P1. durable-worker-receipt-hydration

- User-visible promise: A restarted or resumed agentic workflow hydrates prior
  worker receipts from the ledger and `.handoff/agentic-workers/<task>/` before
  calling the producer.
- Representative action: Run `run_dual_agent_workflow` with required agentic
  policy after a prior worker receipt exists durably.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Existing passing worker receipts are included in P13 input;
  the producer reports `skipped_existing_receipts` or only launches missing
  workers.
- Forbidden outcomes: The producer starts a second worker for a role/worker that
  already has a passing replay-verifiable receipt.

P2. partial-fanout-resume

- User-visible promise: Partial fan-out resumes reuse completed workers and only
  spawn missing or failed roles.
- Representative action: One required role has a passing durable receipt and a
  second required role is absent or failed.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: The existing role is reused; only the missing/failed role is
  launched; total spawned workers across attempts equals the needed roster.
- Forbidden outcomes: Full fan-out repeats, passing workers are respawned, or
  failed receipts are counted as completion.

P3. detached-reattach-no-duplicate-fanout

- User-visible promise: A detached agentic submit retried with the same
  `client_token` returns the same job and does not launch a second workflow or
  second worker fan-out.
- Representative action: Submit a required agentic workflow job, simulate a
  disconnect, and submit the same payload/token again.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Same `job_id`, one CLI launch, one fan-out roster's worth of
  workers, and catch-up returns the worker events since the stored cursor.
- Forbidden outcomes: A new job id, duplicate CLI launch, duplicate worker
  receipts for already completed roles, or missed catch-up events.

P4. orphan-cleanup-before-resume

- User-visible promise: A dead detached attempt does not leave stale worker
  subprocesses running after the next resume/watchdog cleanup.
- Representative action: Persist worker runtime records with live/stale pids,
  then resume or run cleanup for the task.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Timed-out live pids are terminated and logged; active
  in-budget pids are left alone; missing/dead pids are recorded as skipped.
- Forbidden outcomes: No cleanup event, killing in-budget workers, or requiring
  ad hoc shell cleanup outside the supervisor.

P5. terminal-outcome-and-catch-up-preserved

- User-visible promise: The detached workflow's final result remains readable
  from the ledger, and catch-up returns worker fan-out events after disconnect.
- Representative action: Delete `result.json` after terminal completion and poll
  the job; call catch-up from an older cursor.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Poll returns the S3a ledger outcome; catch-up returns worker
  production/progress and terminal outcome events in ascending event id order.
- Forbidden outcomes: Result loss when `result.json` is absent, non-monotonic
  catch-up, or worker events visible only in local logs.

P6. quality-gates-unchanged

- User-visible promise: Agentic detached hardening does not enable fan-out by
  default or weaken P1/P2/P3/P13/P14/Cursor review.
- Representative action: Run default workflow and required-policy failure
  fixtures.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Default policy remains off; required policy still blocks on
  insufficient, failed, self/solo, or non-runtime-native evidence; Cursor review
  remains downstream.
- Forbidden outcomes: Missing verdict counted as accept, fan-out defaulting on,
  or required policy accepting failed/self-reported receipts.

## Out Of Scope

- Enabling agentic fan-out by default.
- Rewriting P13/P14, Cursor review, S2, S3a, S5, or full event sourcing.
- Building the eval harness or reviewer rename work.
- Changing lead_direct defaults or gate order.
