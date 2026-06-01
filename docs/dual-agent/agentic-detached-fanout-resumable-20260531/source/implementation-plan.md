# Implementation Plan: Agentic Detached Fan-Out Resume Safety

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_stdio.py`: hydrate durable worker receipts before
  agentic production, emit worker progress events, and preserve S2/S3a/S5
  reconnect behavior.
- `mcp_tools/codex_supervisor_workflow_cli.py`: inspect only if detached job
  request/terminal persistence needs an argument pass-through; avoid changing
  CLI semantics unless the public-boundary test requires it.
- `supervisor/agentic_executor.py`: merge hydrated receipts with caller
  receipts, dedupe by receipt/worker identity, and run only missing roles.
- `supervisor/agentic_workers.py`: persist worker runtime metadata and add
  task-level stale-worker discovery/cleanup.
- `supervisor/dynamic_workflow_receipts.py`: reuse existing P13 verification and
  evidence-grade derivation for hydrated receipts; do not duplicate validators.
- `supervisor/state.py`: inspect event read/write allowlists for progress events
  if the existing generic event log is not sufficient.
- `tests/test_agentic_executor.py`: add hydration and partial-resume helper
  coverage after the workflow-boundary test.
- `tests/test_agentic_workers.py`: add task cleanup discovery and stale pid
  handling coverage.
- `tests/test_dual_agent_workflow_driver.py`: add public-boundary workflow,
  detached reattach, catch-up, and degraded-review regressions.

## Planned Changes

1. Add durable worker receipt hydration helpers. Read prior worker production
   receipts from the event ledger and reconstruct replay-verifiable receipts
   from `.handoff/agentic-workers/<task>/<worker>/` files.
2. Merge hydrated receipts with caller-supplied receipts before agentic
   production and P13 validation. Deduplicate by receipt id / worker identity.
3. Update `produce_agentic_worker_receipts` so passing existing workers satisfy
   required roles and minimum counts. It should skip all work only when existing
   passing receipts already satisfy the policy; otherwise it should launch only
   missing or failed roles.
4. Persist worker runtime metadata when spawning real worker subprocesses and
   add a task-level orphan cleanup helper that discovers stale worker records.
5. Emit `dual_agent_agentic_worker_progress` events for worker spawned/finished
   progress and include that event kind in transcript/catch-up projections.
6. Add workflow-level tests for durable hydration, detached reconnect, terminal
   ledger poll, and catch-up. Add helper tests for partial resume and cleanup.
7. Run focused tests and `uv run --extra dev pytest -q`.

## Guardrails

- Do not enable fan-out by default.
- Do not count failed, timeout, solo, self-reported, or lead-authored receipts as
  accepted runtime-native evidence.
- Do not bypass P1/P2/P3/P13/P14 or Cursor review.
- Do not duplicate S2/S3a/S5 implementations; reuse the existing submit,
  terminal outcome, and catch-up paths.

## Risks

- Hydrating stale or malformed `.handoff` files could accidentally make failed
  worker evidence look complete; mitigate by routing every hydrated receipt
  through the existing P13 hash/ref verifier and counting only passing terminal
  `dynamic_subagent_result` receipts.
- Partial fan-out filtering could under-spawn required roles if role aliases or
  receipt identities are deduped too aggressively; mitigate with tests that
  include one completed role, one missing role, and one failed receipt for a
  required role.
- Worker cleanup can be destructive if it terminates active in-budget workers;
  mitigate with runtime metadata that records deadlines/status and tests for
  stale live, active live, and dead pids.
- Progress events are observability, not evidence authority; mitigate by keeping
  P13 based on terminal receipt refs and hashes while catch-up uses progress for
  client continuity only.

## Acceptance Map

- P1/P2: hydration and partial-resume tests.
- P3/P5: detached reconnect/catch-up/terminal outcome test.
- P4: task-level orphan cleanup test.
- P6: existing required-policy, default-off, and P13 verification regressions.

## Traceability

- P1 maps to
  `test_run_dual_agent_workflow_hydrates_durable_agentic_worker_receipts_before_producer`
  and the `supervisor/agentic_executor.py` hydration merge.
- P2 maps to
  `test_produce_agentic_worker_receipts_reuses_completed_workers_and_spawns_missing_roles`
  and role-aware partial fan-out filtering.
- P3 maps to
  `test_detached_agentic_reconnect_reuses_job_hydrates_receipts_and_catches_up`
  at the `mcp_tools/codex_supervisor_stdio.py` tool API boundary.
- P4 maps to
  `test_agentic_worker_task_cleanup_discovers_and_reaps_stale_runtime_records`
  in `supervisor/agentic_workers.py`.
- P5 maps to `test_agentic_worker_progress_events_are_available_to_catch_up`
  and the detached reconnect test's terminal ledger poll assertion.
- P6 maps to
  `test_hydrated_runtime_native_receipts_still_hash_verify_and_required_policy_blocks_failures`
  plus the existing required-policy and default-off regression tests.
