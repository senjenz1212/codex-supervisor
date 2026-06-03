# Test Evidence: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

## Local Validation

- `uv run python - <<'PY' ... validate_planning_artifacts(...) ... PY`
  - Result: accepted.
  - Covered: PRD, issues, TDD, grill findings, implementation plan, aggregate
    distinctness, and traceability checks.
- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job tests/test_dual_agent_workflow_driver.py::test_submit_dual_agent_workflow_job_dedupes_same_client_token tests/test_dual_agent_workflow_driver.py::test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome tests/test_dual_agent_workflow_driver.py::test_poll_dual_agent_workflow_job_reads_ledger_result_when_result_file_deleted tests/test_dual_agent_workflow_driver.py::test_workflow_cli_records_terminal_outcome_in_ledger tests/test_state_event_ledger.py -q`
  - Result: 14 passed in 0.29s.
- `git diff --check`
  - Result: passed.

## Diff Boundary

This slice is documentation-only. The intended committed files are the durable
workflow-job extraction plan under `docs/` plus the supervised planning
artifacts under `docs/dual-agent/durable-workflow-job-extraction-plan-20260603/`.
No source files under `supervisor/`, `mcp_tools/`, `tests/`, `scripts/`, or
`config` are intentionally changed.

## Supervised Workflow

- run_id: `3bd54516-a9e3-4eea-9bd2-f58a79a0d693`
- accepted job: `workflow-8cd0534aee49`
- recovery notes:
  - Initial MCP submit transport dropped twice before returning a job id.
  - Local durable submit spawned `workflow-38ede64471ce`; it blocked at
    `workflow_start` because the live roster planner returned nonzero and P13
    correctly saw zero worker receipts.
  - Four real supervisor-owned read-only worker receipts were created under
    `.handoff/agentic-workers/durable-workflow-job-extraction-plan-20260603/`.
  - `workflow-b8ea97e53856` hydrated the receipts but its detached worker died
    on a Telegram milestone `httpx.ReadTimeout`; that was process/transport
    failure, not a verdict.
  - `workflow-8cd0534aee49` reran with a scratch no-Telegram config, hydrated
    the same four receipts, and accepted through `outcome_review`.
- terminal event: `dual_agent_workflow_terminal_outcome` at event id `475317`,
  status `accepted`.

## Agentic Worker Receipts

- `agentic-worker-surface-map`: role `call_site_map`, permission `readOnly`,
  status `passed`.
- `agentic-worker-dependency-graph`: role `dependency_import_graph`, permission
  `readOnly`, status `passed`.
- `agentic-worker-boundary-review`: role `move_vs_keep_boundary`, permission
  `readOnly`, status `passed`.
- `agentic-worker-test-inventory`: role `behavior_pinning_test_inventory`,
  permission `readOnly`, status `passed`.
