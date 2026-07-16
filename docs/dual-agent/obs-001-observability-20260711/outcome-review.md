# OBS-001 Outcome Review

## Verdict

Accepted for the requested focused implementation scope.

OBS-001 now connects workflow submission to target-session rollout ingestion,
normalizes captured nested lifecycle events into canonical kinds, reaches
terminal run state, and opens semantic drift adjudication without requiring a
path violation.

## Verified Trace

1. `submit_dual_agent_workflow_job` resolves the target session from an
   explicit argument, target-specific environment identity, or the workflow ID
   fallback.
2. `register_submitted_workflow` calls the existing public
   `State.register_run` API with the workflow run ID and target session ID.
3. The same registration writes an atomic session-keyed sidecar containing the
   workflow, task, target, scope, and join metadata.
4. `RolloutWatcher` loads that sidecar, stores each retained raw event under
   the workflow run ID, and enriches it with workflow/task/session provenance.
5. Nested Claude/Codex/OpenCode lifecycle shapes normalize to the shared
   taxonomy. A canonical terminal kind updates the run and queues one
   `evaluate_run` decision.
6. `DriftDetector` evaluates semantic signals independently of the L1 path
   threshold. Goal abandonment in an allowed file produces L1/L2/L3 evidence
   with zero scope violations and queues `adjudicate_drift`.

The end-to-end join test confirms all captured rollout events resolve to
exactly one task, one workflow run, and one workflow job.

## Focused Verification

Command:

```text
uv run pytest -q \
  tests/test_obs_001_observability.py \
  tests/test_rollout_watcher_live.py \
  tests/test_telegram_progress_streaming.py \
  tests/test_drift_detector_rewire.py \
  tests/test_drift_replay.py \
  tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_reserves_and_poll_is_read_only \
  tests/test_dual_agent_workflow_driver.py::test_public_run_dual_agent_workflow_mcp_tool_is_non_blocking_submit_shim \
  tests/test_dual_agent_workflow_driver.py::test_submit_workflow_job_payload_round_trips_agentic_policy_fields \
  tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_dedupes_same_client_token \
  tests/test_codex_supervisor_axi.py::test_axi_submit_status_share_idempotency_and_sanitize_receipts \
  tests/test_codex_supervisor_axi.py::test_axi_submit_then_detached_dispatcher_writes_request_and_spawns
```

Final result: `46 passed in 1.88s`.

Additional checks:

- `python3 -m py_compile` on all five owned Python integration modules: passed.
- `git diff --check`: passed.

## Scope Review

- No `state.py` edit was made for OBS-001; registration uses its existing
  public API.
- Raw rollout payloads remain intact for replay and diagnosis.
- Existing callback consumers retain raw wrapper kinds and receive the
  canonical kind separately as `normalized_kind`.
- Same-token submission retries preserve the originally reserved target
  session.
- Captured fixtures preserve nested source structure and are sanitized as
  documented in `fixture-provenance.md`.

## Limitations and Blockers

- Historical stale runs are not backfilled or closed; that is an explicit
  non-goal of this slice.
- Focused tests use sanitized captured events and an isolated state database;
  no long-lived daemon restart or production state migration was performed.
- No external AXI/Cursor reviewer receipt was produced in this direct
  implementation run.
- No commit was created, per the task instruction.
