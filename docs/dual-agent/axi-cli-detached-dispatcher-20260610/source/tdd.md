# TDD Plan: Detached Dispatcher And AXI CLI

## test_submit_dual_agent_workflow_job_reserves_and_poll_is_read_only

Maps to: Slice AXI-1, P1, P2

Red: Polling a newly reserved job calls dispatcher code, writes `request.json`, and spawns a worker. The test monkeypatches dispatcher construction to raise and fails if poll drives any phase.

Green: Submit returns `submitted/reserved`; poll returns the same durable state with no request file and no process spawn. A separate `WorkflowJobDispatcher.run_once(job_id=...)` then writes the request and persists `spawned`.

## test_poll_dual_agent_workflow_job_concurrent_request_written_is_read_only

Maps to: Slice AXI-1, P1

Red: Concurrent poll callers race to spawn or mutate a `request_written` job. At least one call changes pid or recovery point.

Green: All poll callers return `submitted/request_written`; pid, claim token, and recovery point remain unchanged until the dispatcher claims the row.

## test_axi_home_view_toon_json_empty_states_and_help

Maps to: Slice AXI-2, P3, P4

Red: The CLI no-args path emits ambiguous empty output or JSON missing total counts and help lines.

Green: TOON output includes `jobs[0] -- none pending`, `gates[0] -- none active`, and `help[]`; JSON has equivalent status and totals.

## test_axi_submit_status_share_idempotency_and_sanitize_receipts

Maps to: Slice AXI-2, P5

Red: CLI submit stores caller-stamped supervisor runtime receipts or creates multiple rows for the same stable token.

Green: The stored request downgrades forged provenance, duplicate submit reattaches to the same job, and status reports `submitted/reserved`.

## test_axi_submit_then_detached_dispatcher_writes_request_and_spawns

Maps to: Slice AXI-2, Slice AXI-3, P2, P3

Red: AXI submit either writes the request in-process or the dispatcher cannot target and spawn the submitted job.

Green: AXI submit only reserves; targeted dispatcher `run_once` writes the request, spawns the fake worker, and poll reports `running/spawned` from the ledger.

## test_dispatcher_cli_once_can_target_job_id

Maps to: Slice AXI-3, P2

Red: `codex-supervisor-workflow-dispatcher --once` can only claim the oldest job, making targeted recovery unsafe when older reserved jobs exist.

Green: `--once --job-id job-target` passes that id into `run_once(job_id=...)` and reports the targeted dispatch result.

## test_axi_fields_lessons_and_trends_are_read_only_observational

Maps to: Slice AXI-2, Slice AXI-4, P3, P4

Red: The AXI read surfaces lack direct coverage for `--fields`, `lessons`, and `trends`, so a CLI change can hide unbounded output or accidentally write gate-authoritative events.

Green: Seeded lesson and trend rows are rendered through public CLI commands; `--fields` narrows TOON lesson output, `trends --json` returns the stored metric summary, and the event count is unchanged after both read-only commands.

## test_axi_structured_errors_stdout_exit_one

Maps to: Slice AXI-2, P3, P4

Red: Invalid CLI usage exits through argparse stderr without structured stdout.

Green: CLI errors return exit code 1 with JSON or TOON `error.code`, `error.message`, and `help[]`.

## test_axi_console_script_is_registered

Maps to: Slice AXI-2, P3

Red: The AXI module exists but `pyproject.toml` does not expose the `codex-supervisor-axi` console script.

Green: The project script table maps `codex-supervisor-axi` to `mcp_tools.codex_supervisor_axi:main`.

## test_poll_dual_agent_workflow_job_leaves_result_file_recovery_to_dispatcher

Maps to: Slice AXI-1, Slice AXI-3, P1, P2

Red: Poll ingests result files and completes jobs, making status reads another execution phase.

Green: Poll reports the running row while the dispatcher reaper owns result-file recovery; after the dispatcher persists terminal outcome, poll replays the ledger result.
