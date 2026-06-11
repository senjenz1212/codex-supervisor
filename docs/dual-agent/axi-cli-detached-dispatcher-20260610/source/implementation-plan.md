# Implementation Plan: Detached Dispatcher And AXI CLI

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_stdio.py` removes poll-side dispatcher invocation and duplicate phase-driving helpers.
- `mcp_tools/codex_supervisor_axi.py` adds the AXI CLI adapter around the existing supervisor API.
- `supervisor/workflow_job_dispatcher.py` adds targeted `--job-id` support for one-shot dispatcher dispatch.
- `supervisor/state.py` adds read-only listing helpers for active jobs, active gates, lessons, and trends output.
- `pyproject.toml` registers `codex-supervisor-axi`.
- `tests/test_codex_supervisor_axi.py` covers CLI behavior, idempotency, provenance, read-only lessons/trends, fields projection, and dispatcher handoff.
- `tests/test_dual_agent_workflow_driver.py` flips poll tests from execution to read-only status and keeps dispatcher spawn coverage.
- `docs/supervisor-axi-detached-dispatcher.md` documents the dispatcher daemon command and plist path.

## Risks

- Removing poll-side terminal reconciliation could reveal jobs that previously relied on poll to complete them; this is intentional, and dispatcher/worker terminal persistence must be watched in production.
- Adding a CLI can create semantic drift if it grows independent logic; this plan keeps it thin over `CodexSupervisorMcpAPI` and tests ledger outcomes.
- Targeted `--job-id` must not become a multi-claimer substitute; it is limited to `--once` and the long-lived dispatcher remains the production lane.
- Existing pending jobs can stay reserved if no daemon is running; the home view and help lines surface that condition explicitly.

## Traceability

- P1 is covered by `test_submit_dual_agent_workflow_job_reserves_and_poll_is_read_only`, `test_poll_dual_agent_workflow_job_concurrent_request_written_is_read_only`, and `test_poll_dual_agent_workflow_job_leaves_result_file_recovery_to_dispatcher`.
- P2 is covered by `test_axi_submit_then_detached_dispatcher_writes_request_and_spawns` and `test_dispatcher_cli_once_can_target_job_id`.
- P3 is covered by `test_axi_home_view_toon_json_empty_states_and_help`, `test_axi_fields_lessons_and_trends_are_read_only_observational`, `test_axi_structured_errors_stdout_exit_one`, and `test_axi_console_script_is_registered`.
- P4 is covered by `test_axi_home_view_toon_json_empty_states_and_help` and `test_axi_fields_lessons_and_trends_are_read_only_observational`.
- P5 is covered by `test_axi_submit_status_share_idempotency_and_sanitize_receipts`.
- P6 is covered by the dispatcher documentation file and CLI registration tests.
