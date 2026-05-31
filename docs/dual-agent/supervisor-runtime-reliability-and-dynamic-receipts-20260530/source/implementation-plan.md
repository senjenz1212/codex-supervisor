# Implementation Plan

## Files / Modules To Touch

- `daemon.py`: wrap restartable subsystems and keep hook server fatal.
- `supervisor/runtime_health.py`: record subsystem health, restart recoverable loops, and expose fatal hook-server failures before reraising.
- `supervisor/rollout_watcher.py`: guard drain, sweep, and callback failures.
- `supervisor/state.py`: serialize `write_event`, commit rollout event/offset pairs atomically, and expose the new P13 event kind.
- `supervisor/dynamic_workflow_receipts.py`: implement deterministic P13 validation.
- `mcp_tools/codex_supervisor_stdio.py`: wire P13 into workflow and direct gate surfaces.
- `supervisor/dual_agent_artifacts.py`: render P13 events and TDD grill source links.
- `supervisor/failure_taxonomy.py`: classify P13 failures as governance provenance gaps.
- `tests/test_runtime_health.py`: cover restartable subsystem survival.
- `tests/test_rollout_watcher_live.py`: cover callback and sweep guardrails.
- `tests/test_state_event_ledger.py`: cover concurrent parent writes.
- `tests/test_dynamic_workflow_receipts.py`: cover P13 validator behavior.
- `tests/test_dual_agent_workflow_driver.py`: cover workflow P13 behavior.
- `tests/test_codex_supervisor_mcp_stdio.py`: cover direct gate P13 behavior.
- `tests/test_dual_agent_artifacts.py`: cover P13 markdown and source navigation.
- `docs/testing/public-boundaries.md`: add runtime and ledger boundaries.
- `docs/testing/dual-agent-slice0-coverage-index.md`: add P13 coverage.

## Risks

- Direct-gate P13 enforcement can add repeated P13 validation events during a full dynamic workflow because each gate invokes the same public surface. This is traceable but noisier.
- Receipt validation is shape-based, not replay execution. It checks required references and metadata but does not open transcript files or verify hashes in this slice.
- Process-local event serialization protects one daemon process using one `State` object. Cross-process writes still rely on SQLite WAL and autoincrement rather than a custom distributed sequencer.
- Callback failure handling is intentionally at-most-once for progress notifications: the rollout line is not replayed after a callback exception.
- Malformed complete rollout lines are skipped after a durable parse-health event; the original malformed line is not replayed.

## Traceability

- P1 -> `test_restartable_subsystem_records_failure_and_restarts`
- P1 -> `test_fatal_subsystem_records_clean_return_as_failed`
- P1 -> `test_fatal_subsystem_records_exception_before_reraising`
- P2 -> `test_rollout_watcher_callback_failure_records_health_without_replaying_line`
- P2 -> `test_rollout_watcher_malformed_json_records_health_and_advances_offset`
- P2 -> `test_rollout_watcher_read_failure_records_health`
- P2 -> `test_rollout_watcher_guarded_sweep_records_failure_and_continues`
- P3 -> `test_state_write_event_serializes_concurrent_parent_writes`
- P2/P3 -> `test_state_can_commit_event_and_tail_offset_together`
- P4 -> `test_verify_dynamic_workflow_receipts_rejects_missing_preview_gates`
- P4 -> `test_verify_dynamic_workflow_receipts_accepts_complete_preview_manifest`
- P4 -> `test_verify_dynamic_workflow_receipts_rejects_names_only_manifest`
- P4 -> `test_run_dual_agent_workflow_blocks_dynamic_preview_without_p13_receipts`
- P4 -> `test_start_dual_agent_gate_blocks_dynamic_preview_without_p13_receipts`
- P5 -> `test_read_gate_transcript_includes_dynamic_workflow_receipt_validation`
- P5 -> `test_export_dual_agent_run_artifacts_renders_dynamic_workflow_receipt_validation`
- P5 -> `test_export_dual_agent_run_artifacts_links_tdd_grill_source_artifact`
