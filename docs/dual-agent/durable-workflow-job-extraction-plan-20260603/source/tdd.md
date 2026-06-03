# TDD Plan: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

## Public Boundary

This is a documentation-only slice. The public boundary is the generated
design document plus the existing durable workflow-job tests that the document
names as the safety net for the follow-up extraction.

## test_durable_workflow_job_plan_lists_public_transport_surface

Maps to: Slice 1, PRD promise P1

RED: The design document is absent or does not name the submit, poll, catch-up,
run workflow, CLI worker, idempotency, terminal reconciliation, and ledger event
surfaces.

GREEN: `docs/durable-workflow-job-extraction-plan.md` exists and contains the
complete surface inventory with paths and behavior notes.

## test_durable_workflow_job_plan_preserves_mcp_adapter_boundary

Maps to: Slice 2, PRD promise P2

RED: The plan suggests moving MCP method declarations, renaming tools, or
moving SQLite schema ownership out of `supervisor/state.py`.

GREEN: The move-vs-keep table keeps MCP wrappers and state persistence in
place while proposing a new lifecycle service module for implementation logic.

## test_durable_workflow_job_plan_names_behavior_pinning_tests

Maps to: Slice 3, PRD promise P3

RED: The plan lacks concrete tests for detached spawn, payload round-trip,
idempotent dedupe, reconnect catch-up, ledger-first poll, CLI persistence,
state migrations, and event-tail reads.

GREEN: The plan names the exact existing pytest functions and groups them by
behavior so the follow-up refactor has a regression checklist.

## test_durable_workflow_job_plan_is_doc_only

Maps to: Slice 4, PRD promise P5

RED: The diff changes source files under `supervisor/`, `mcp_tools/`, `tests/`,
`scripts/`, or `config`.

GREEN: The committed diff contains only documentation and dual-agent planning
artifacts for this task.

## Validation Commands

- `uv run python - <<'PY' ... validate_planning_artifacts(...) ... PY`
- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_dedupes_same_client_token tests/test_dual_agent_workflow_driver.py::test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome tests/test_dual_agent_workflow_driver.py::test_poll_dual_agent_workflow_job_reads_ledger_result_when_result_file_deleted tests/test_dual_agent_workflow_driver.py::test_workflow_cli_records_terminal_outcome_in_ledger tests/test_state_event_ledger.py -q`
- `git diff --check`
