# Durable Workflow Job Extraction Plan

## Purpose

This plan maps the behavior-preserving extraction surface for durable
dual-agent workflow jobs. It is intentionally documentation-only: no source code
is moved in this slice. The follow-up implementation can extract lifecycle
logic into `supervisor/durable_workflow_job.py` while keeping public MCP tool
names, payloads, storage semantics, and gate behavior unchanged.

## Current Surface

The durable workflow job lifecycle currently spans four clusters.

1. MCP adapter and workflow driver in `mcp_tools/codex_supervisor_stdio.py`

- `CodexSupervisorMcpAPI.run_dual_agent_workflow(...)` is the synchronous
  supervisor-owned workflow path. It canonicalizes execution mode, reviewer
  settings, agentic lead policy, planning artifacts, screenshots, tool
  receipts, and task complexity before running the gate sequence.
- `CodexSupervisorMcpAPI.submit_dual_agent_workflow_job(...)` builds the
  detached request payload, derives or hashes the `client_token`, reserves a
  durable job row, writes `.handoff/workflow-jobs/<job_id>/request.json`,
  spawns `mcp_tools.codex_supervisor_workflow_cli` in a new session, records a
  `dual_agent_workflow_job` ledger event, and returns a `job_id` plus
  `poll_tool`.
- `CodexSupervisorMcpAPI.poll_dual_agent_workflow_job(...)` reads the job row,
  reconciles the ledger terminal outcome with the result-file cache, persists
  a result-file outcome when the ledger has not seen one yet, fails closed when
  the worker pid is gone without a result, emits terminal discrepancy events
  when cache and ledger differ, and returns a resume prompt with the poll
  payload.
- `CodexSupervisorMcpAPI.catch_up_dual_agent_workflow(...)` reads event-tail
  pages from `State.read_events_since(...)` by caller-supplied `last_event_id`
  and returns monotonic event IDs, count, next cursor, and `has_more`.
- The MCP tool declarations near the bottom of the file expose
  `run_dual_agent_workflow`, `submit_dual_agent_workflow_job`,
  `poll_dual_agent_workflow_job`, and `catch_up_dual_agent_workflow` by
  delegating to the `CodexSupervisorMcpAPI` methods.

2. Detached CLI worker in `mcp_tools/codex_supervisor_workflow_cli.py`

- `workflow_kwargs_from_payload(...)` filters request JSON to the allowed
  workflow keys.
- `run_workflow_payload(...)` constructs `CodexSupervisorMcpAPI` and calls the
  same `run_dual_agent_workflow(...)` boundary used by MCP.
- `persist_detached_workflow_terminal_outcome(...)` writes terminal status and
  terminal outcome back to the `dual_agent_workflow_jobs` row by calling
  `State.complete_dual_agent_workflow_job(...)`.
- `main(...)` loads config, secrets, and Codex MCP env, runs the workflow,
  persists terminal outcome, writes result JSON, and exits nonzero only when
  `--fail-on-blocked` asks it to.

3. Durable state in `supervisor/state.py`

- The `dual_agent_workflow_jobs` table stores `job_id`, `run_id`, `task_id`,
  `cwd`, `status`, `pid`, request/result/log paths, `idempotency_token`,
  terminal status, redacted terminal outcome JSON, return code, error, and
  timestamps.
- `reserve_dual_agent_workflow_job(...)` is the submit dedupe boundary. It uses
  an immediate transaction and returns an existing row when the idempotency
  token is already present, preventing a duplicate detached process.
- `upsert_dual_agent_workflow_job(...)` records the running pid and paths after
  spawn.
- `complete_dual_agent_workflow_job(...)` atomically updates job status,
  terminal fields, return code, error, and appends a
  `dual_agent_workflow_terminal_outcome` event. It stores redacted canonical
  terminal JSON at rest.
- `read_events_since(...)` is the catch-up substrate. It reads events after an
  event cursor in ascending event ID order and tolerates non-contiguous IDs.
- `read_dual_agent_gate_events(...)` includes job, terminal outcome, terminal
  discrepancy, route, interaction, reviewer, and agentic worker events so
  artifacts and transcripts can render the workflow-job trail.

4. Artifact and transcript readers

- `read_gate_transcript(...)` in `mcp_tools/codex_supervisor_stdio.py` includes
  `dual_agent_workflow_job`, `dual_agent_workflow_terminal_outcome`, and
  `dual_agent_workflow_terminal_discrepancy` in `workflow_jobs`.
- `supervisor/dual_agent_artifacts.py` consumes the event ledger and exports
  human-readable workflow artifacts; it should not need a direct dependency on
  a new durable job service.

## Proposed Module Boundary

Create `supervisor/durable_workflow_job.py` in the follow-up slice. Keep it a
small service module, not a framework. Suggested public surface:

- `build_workflow_job_payload(...)`: canonicalize the submit payload from the
  same semantic knobs used today.
- `workflow_job_idempotency_token(run_id, payload, client_token)`: move the
  existing canonical payload and token logic unchanged.
- `submit_workflow_job(...)`: reserve the job, write request JSON, spawn the
  detached CLI, upsert running pid, emit job events, and return the existing
  response shape.
- `poll_workflow_job(...)`: implement the ledger-result, result-file fallback,
  pid-dead failure, terminal discrepancy, completion event, and resume-prompt
  assembly currently in `poll_dual_agent_workflow_job`.
- `catch_up_workflow(...)`: assemble the catch-up response around
  `State.read_events_since(...)`.
- `persist_terminal_outcome(...)`: share terminal outcome persistence between
  the detached CLI and the service.

The module should accept explicit dependencies (`State`, config path,
`subprocess.Popen`, `_pid_alive`, and resume-prompt callback) so tests can keep
using fakes without importing MCP server construction.

## Move Vs Keep

Move to `supervisor/durable_workflow_job.py` in the next slice:

- `_canonical_workflow_job_payload(...)`
- `_workflow_job_idempotency_token(...)`
- detached request payload construction for submit
- `.handoff/workflow-jobs/<job_id>/request.json` path construction
- `subprocess.Popen` launch of `mcp_tools.codex_supervisor_workflow_cli`
- submit failure handling that completes the job with a terminal failed outcome
- poll reconciliation between `terminal_outcome_json` and `result.json`
- ledger-wins terminal discrepancy detection and event emission
- pid-dead-without-result failure handling
- catch-up response assembly
- detached CLI terminal outcome persistence helper, or a shared helper invoked
  by the CLI

Keep in `mcp_tools/codex_supervisor_stdio.py`:

- MCP tool declarations and signatures
- `CodexSupervisorMcpAPI.run_dual_agent_workflow(...)`
- adapter-level config normalization for reviewers, execution layer, and
  agentic lead policy until a separate workflow-service extraction exists
- `read_gate_transcript`, `read_outcome`, `export_gate_artifacts`, budget and
  escalation tools
- planning artifact preflight and reviewer-panel gate logic

Keep in `supervisor/state.py`:

- SQLite schema and migrations
- `dual_agent_workflow_jobs` table ownership
- `reserve_dual_agent_workflow_job(...)`
- `upsert_dual_agent_workflow_job(...)`
- `complete_dual_agent_workflow_job(...)`
- `get_dual_agent_workflow_job(...)`
- `read_events_since(...)`

Keep in `mcp_tools/codex_supervisor_workflow_cli.py`:

- CLI argument parsing
- config, secrets, and Codex MCP env loading
- stdout/result-file behavior
- process exit-code policy

The CLI can import the new service helper for terminal persistence, but it
should remain the executable worker entrypoint.

## Call-Site Impact

- MCP clients: no change. The tool names, argument names, and response fields
  remain `run_dual_agent_workflow`, `submit_dual_agent_workflow_job`,
  `poll_dual_agent_workflow_job`, and `catch_up_dual_agent_workflow`.
- Detached CLI callers: no change. Request JSON still flows through
  `workflow_kwargs_from_payload(...)` and writes the same result JSON.
- `CodexSupervisorMcpAPI`: submit, poll, and catch-up methods become thin
  adapters that pass normalized dependencies to the service. `run_dual_agent_workflow`
  remains in place for this extraction.
- `State`: no API change. The extracted service calls the same storage methods.
- Artifact export and transcript readers: no direct API change. They continue
  reading ledger events from `State`.
- Tests: existing behavior tests should move only if they become easier to
  express against the service. Keep public-boundary MCP tests in place.

## Behavior-Pinning Test Inventory

Current tests that should stay green before, during, and after extraction:

- `tests/test_dual_agent_workflow_driver.py::test_workflow_cli_payload_runs_same_supervisor_api`
  pins the CLI fallback calling the same supervisor API as MCP.
- `tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job`
  pins detached worker spawn, new session launch, request JSON, job row, and
  transcript visibility.
- `tests/test_dual_agent_workflow_driver.py::test_submit_workflow_job_payload_round_trips_agentic_policy_fields`
  pins agentic policy fields in detached request payloads.
- `tests/test_dual_agent_workflow_driver.py::test_submit_workflow_job_payload_round_trips_reviewer_infra_retry_policy`
  pins reviewer retry fields in detached request payloads.
- `tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_dedupes_same_client_token`
  pins explicit `client_token` idempotency and one subprocess spawn.
- `tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_derives_idempotency_for_legacy_callers`
  pins derived idempotency for callers without a client token.
- `tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_derived_tokens_differ_for_different_payloads`
  pins different semantic payloads spawning different jobs.
- `tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_keeps_different_tokens_independent`
  pins independent client tokens.
- `tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_concurrent_same_token_launches_once`
  pins concurrent submit dedupe.
- `tests/test_dual_agent_workflow_driver.py::test_catch_up_dual_agent_workflow_returns_cursor_page`
  pins catch-up event paging and cursor behavior.
- `tests/test_dual_agent_workflow_driver.py::test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome`
  pins reconnect protocol, idempotent re-submit, missed event replay, and
  terminal outcome polling from the ledger.
- `tests/test_dual_agent_workflow_driver.py::test_poll_dual_agent_workflow_job_reads_durable_result_after_transport_loss`
  pins result-file fallback and terminal persistence.
- `tests/test_dual_agent_workflow_driver.py::test_poll_dual_agent_workflow_job_reads_ledger_result_when_result_file_deleted`
  pins ledger terminal outcome as durable source after cache loss.
- `tests/test_dual_agent_workflow_driver.py::test_workflow_cli_records_terminal_outcome_in_ledger`
  pins detached CLI terminal outcome persistence.
- `tests/test_dual_agent_workflow_driver.py::test_poll_dual_agent_workflow_job_ledger_wins_over_result_file_cache`
  pins ledger-wins reconciliation and discrepancy event emission.
- `tests/test_dual_agent_workflow_driver.py::test_poll_dual_agent_workflow_job_does_not_emit_discrepancy_for_matching_cache`
  pins no noisy discrepancy event for matching cache.
- `tests/test_dual_agent_workflow_driver.py::test_complete_dual_agent_workflow_job_requires_terminal_outcome`
  pins fail-closed validation for empty terminal outcomes.
- `tests/test_dual_agent_workflow_driver.py::test_complete_dual_agent_workflow_job_rolls_back_status_when_terminal_event_fails`
  pins atomic rollback on terminal event write failure.
- `tests/test_dual_agent_workflow_driver.py::test_complete_dual_agent_workflow_job_redacts_terminal_outcome_at_rest`
  pins redacted terminal outcome storage.
- `tests/test_state_event_ledger.py::test_read_events_since_returns_ascending_tail_after_cursor`,
  `test_read_events_since_starts_from_beginning_and_empty_tail`, and
  `test_read_events_since_tolerates_non_contiguous_event_ids` pin the catch-up
  event-tail substrate.
- `tests/test_schema_migrations.py::test_forward_migration_adds_workflow_job_terminal_outcome_fields`
  and related migration tests pin the job table's idempotency and terminal
  outcome columns.
- `tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts`
  pins the adjacent required fan-out receipt path that can feed durable
  workflow jobs.
- `tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_hydrates_durable_agentic_worker_receipts_before_producer`
  pins resume behavior for durable worker receipts before producer planning.

## Risks

- Payload drift: splitting request payload construction can change defaults or
  omitted fields. Mitigation: keep payload tests and compare request JSON shape
  before and after extraction.
- Ledger/cache authority drift: poll must continue to prefer
  `terminal_outcome_json` over stale result files. Mitigation: keep the
  ledger-wins and no-discrepancy tests at the MCP boundary.
- Duplicate process spawn: idempotency must reserve before launch. Mitigation:
  keep reservation in `State.reserve_dual_agent_workflow_job(...)` and keep
  concurrent submit tests.
- CLI/MCP skew: the CLI must keep calling the same `run_dual_agent_workflow`
  boundary. Mitigation: leave `workflow_kwargs_from_payload(...)` tests in
  place and move only persistence helpers at first.
- Oversized extraction: moving `run_dual_agent_workflow` itself would mix gate
  orchestration with transport recovery. Mitigation: first extraction only
  submit, poll, catch-up, and terminal persistence.

## Recommended Follow-Up Slice

Implement the extraction in two commits inside one supervised slice:

1. Add `supervisor/durable_workflow_job.py` with service functions copied
   behavior-for-behavior from the current submit, poll, catch-up, token, and
   terminal-persistence helpers. Keep tests green.
2. Change `CodexSupervisorMcpAPI.submit_dual_agent_workflow_job`,
   `poll_dual_agent_workflow_job`, and `catch_up_dual_agent_workflow` into
   thin adapters. Change the CLI to use the shared terminal-persistence helper.

Do not move gate sequencing, reviewer panel logic, artifact preflight, state
schema, or MCP registration in that follow-up.
