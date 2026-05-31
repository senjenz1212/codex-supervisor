# TDD Plan

## Public Boundary Registry

- `supervisor_runtime_loop`: restartable subsystem wrapper used by daemon long-running tasks.
- `event_ingestion_api`: rollout file ingestion through `RolloutWatcher`.
- `supervisor_event_ledger`: `State.write_event` as the parent-side append boundary.
- `codex_supervisor_mcp`: `run_dual_agent_workflow` and `start_dual_agent_gate` over the stdio MCP API.

## test_restartable_subsystem_records_failure_and_restarts

Maps to: Slice 1, P1.

RED: Fake restartable coroutine raises once and the runtime wrapper must record a degraded health event and restart it.

GREEN: Implement `supervisor.runtime_health.run_restartable_subsystem` and wrap recoverable daemon tasks while leaving hook server direct.

## test_fatal_subsystem_records_clean_return_as_failed

Maps to: Slice 1, P1.

RED: A fatal subsystem coroutine that returns normally must still produce a failed health event and raise.

GREEN: Implement `run_fatal_subsystem` and use it for `hook_server`.

## test_fatal_subsystem_records_exception_before_reraising

Maps to: Slice 1, P1.

RED: A fatal subsystem exception must be visible in `supervisor_subsystem_health` before it escapes to stop the daemon.

GREEN: Record failed runtime-exception health inside `run_fatal_subsystem`.

## test_rollout_watcher_callback_failure_records_health_without_replaying_line

Maps to: Slice 2, P2.

RED: A valid rollout event is written, then the callback raises; a second drain must not duplicate the line.

GREEN: Catch `on_event` exceptions after event and offset persistence and write a `supervisor_subsystem_health` event.

## test_rollout_watcher_malformed_json_records_health_and_advances_offset

Maps to: Slice 2, P2.

RED: A complete malformed JSONL line must not be silently discarded or replayed forever.

GREEN: Record `rollout_watcher.parse` health and advance the tail offset in the same commit.

## test_rollout_watcher_read_failure_records_health

Maps to: Slice 2, P2.

RED: A file read failure must be visible in the event ledger without pretending the line was processed.

GREEN: Record `rollout_watcher.drain` read health and leave the tail offset unchanged.

## test_rollout_watcher_guarded_sweep_records_failure_and_continues

Maps to: Slice 2, P2.

RED: A sweep exception must be recorded as health instead of escaping from the periodic loop.

GREEN: Add `guarded_sweep_once` and call it from `_periodic_sweep`.

## test_state_write_event_serializes_concurrent_parent_writes

Maps to: Slice 3, P3.

RED: Two hundred concurrent parent-side event appends through one `State` object must not throw SQLite API misuse or lose rows.

GREEN: Add a `threading.RLock` around `State.write_event`.

## test_state_can_commit_event_and_tail_offset_together

Maps to: Slice 2 and Slice 3, P2 and P3.

RED: A rollout event append and its tail offset update must be represented as one public ledger operation.

GREEN: Add `State.write_event_and_tail_offset`.

## test_verify_dynamic_workflow_receipts_rejects_missing_preview_gates

Maps to: Slice 4, P4.

RED: Dynamic preview with no dynamic receipts must return P13 red with every preview gate listed as missing.

GREEN: Add `supervisor.dynamic_workflow_receipts.verify_dynamic_workflow_receipts`.

## test_verify_dynamic_workflow_receipts_accepts_complete_preview_manifest

Maps to: Slice 4, P4.

RED: Complete dynamic receipts matching the default preview gate tuple must return P13 green.

GREEN: Validate receipt kind, status, subagent metadata, output schema/hash, headless mode, replay/CI evidence, and throwaway comparison evidence.

## test_verify_dynamic_workflow_receipts_rejects_names_only_manifest

Maps to: Slice 4, P4.

RED: A dynamic workflow manifest that only names gates must not satisfy P13.

GREEN: Require per-gate evidence fields instead of treating canonical gate names as proof.

## test_run_dual_agent_workflow_blocks_dynamic_preview_without_p13_receipts

Maps to: Slice 4, P4 and P5.

RED: `run_dual_agent_workflow` with dynamic preview and only ordinary PRD/TDD/test receipts must block at `workflow_start`.

GREEN: Validate P13 at workflow start, write `dual_agent_dynamic_workflow_receipt_validation`, and block before `/lead`.

## test_start_dual_agent_gate_blocks_dynamic_preview_without_p13_receipts

Maps to: Slice 4, P4 and P5.

RED: Direct `start_dual_agent_gate` with dynamic preview must not bypass P13.

GREEN: Add the same dynamic receipt preflight to the direct MCP gate surface.

## test_read_gate_transcript_includes_dynamic_workflow_receipt_validation

Maps to: Slice 4, P5.

RED: Transcript output must expose P13 validation events in a dedicated array.

GREEN: Add the event kind to transcript read-side allowlists and return payloads.

## test_export_dual_agent_run_artifacts_renders_dynamic_workflow_receipt_validation

Maps to: Slice 4, P5.

RED: Markdown export must show P13 validation, required gates, verified gates, missing gates, and receipt ids.

GREEN: Add a dedicated artifact renderer for `dual_agent_dynamic_workflow_receipt_validation`.

## test_export_dual_agent_run_artifacts_links_tdd_grill_source_artifact

Maps to: Slice 4, P5.

RED: Artifact index navigation must expose both PRD grill findings and TDD grill findings.

GREEN: Add the second grill source link and include `source/grill-findings-tdd.md` in generated/mandatory source artifacts.

## Regression Commands

- `uv run --extra dev pytest tests/test_runtime_health.py tests/test_rollout_watcher_live.py tests/test_state_event_ledger.py tests/test_dynamic_workflow_receipts.py tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py tests/test_dual_agent_artifacts.py tests/test_failure_taxonomy.py -q`
- `uv run --extra dev pytest -q`
