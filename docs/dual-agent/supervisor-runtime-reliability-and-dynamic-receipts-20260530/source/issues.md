# Issues

## Slice 1: Restartable Runtime Health Loop

Priority: high

Scope: Add a runtime-health helper and wrap recoverable daemon loops while preserving fatal hook-server semantics.

Acceptance Criteria:

- [ ] Runtime loop failure writes `supervisor_subsystem_health`.
- [ ] Runtime loop restarts with bounded backoff.
- [ ] Cancellation exits cleanly.
- [ ] `hook_server` remains fatal, but clean returns and exceptions write failed health events before reraising.

### PRD Promise

- Promise IDs: P1.
- Public boundary for first RED test: `supervisor_runtime_loop`.
- Representative action: A restartable coroutine fails once, then can restart.
- Allowed outcomes: Health event written; loop restarts after bounded backoff; fatal hook exits are health-visible; cancellation exits cleanly.
- Forbidden outcomes: Exception escapes and kills the parent daemon task.

## Slice 2: Guard Rollout Watcher Callback and Sweep Failures

Priority: high

Scope: Guard rollout watcher callback, drain, and sweep failure paths without changing durable tail-offset semantics.

Acceptance Criteria:

- [ ] Callback failure after event persistence records a health event.
- [ ] Parsed line event and tail offset commit atomically before the callback runs.
- [ ] Malformed complete JSONL lines advance with a durable parse health event.
- [ ] Read failures are recorded as durable health events without advancing offsets.
- [ ] Sweep failure is recorded and does not permanently stop the periodic loop.
- [ ] Existing exactly-once ingestion tests still pass.

### PRD Promise

- Promise IDs: P2.
- Public boundary for first RED test: `event_ingestion_api`.
- Representative action: `RolloutWatcher._drain_file` sees a valid line and the progress callback raises.
- Allowed outcomes: Event and offset are persisted together; health events record callback, parse, and read failures; drain returns normally.
- Forbidden outcomes: Callback exception aborts ingestion.

## Slice 3: Serialize Event Ledger Writes

Priority: high

Scope: Serialize parent-side writes through `State.write_event` using a process-local reentrant lock.

Acceptance Criteria:

- [ ] Concurrent parent writes return unique event ids.
- [ ] Every payload is durably stored.
- [ ] Rollout event and tail-offset updates can be committed as one operation.
- [ ] Trace envelope stamping and redaction still happen inside the append path.
- [ ] Existing read-side transcript behavior is unchanged.

### PRD Promise

- Promise IDs: P3.
- Public boundary for first RED test: `supervisor_event_ledger`.
- Representative action: Many threads append events through one `State` instance.
- Allowed outcomes: Unique event ids and durable payloads for every event.
- Forbidden outcomes: SQLite connection concurrency errors or missing events.

## Slice 4: P13 Dynamic Workflow Receipt Validation

Priority: high

Scope: Add deterministic P13 receipt validation for dynamic workflow preview and expose it in workflow, direct gate, transcript, artifacts, and failure taxonomy.

Acceptance Criteria:

- [ ] Dynamic preview without receipts blocks before Claude invocation.
- [ ] Complete receipts matching `DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES` pass.
- [ ] `dual_agent_dynamic_workflow_receipt_validation` appears in transcripts.
- [ ] Markdown artifact export renders P13 required, verified, and missing gates.
- [ ] Hyphenated `dynamic-workflow-preview` input canonicalizes to `dynamic_workflow_preview`.

### PRD Promise

- Promise IDs: P4, P5.
- Public boundary for first RED test: `codex_supervisor_mcp`.
- Representative action: `run_dual_agent_workflow` requests dynamic workflow preview without dynamic receipts.
- Allowed outcomes: Workflow blocks at `workflow_start`; ledger/transcript/artifacts show the P13 validation.
- Forbidden outcomes: Claude acceptance or Cursor acceptance bypasses missing dynamic receipts.
