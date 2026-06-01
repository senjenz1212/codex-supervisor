# Agentic Executor Wiring Inline Test Evidence

## Focused

Command:

```text
uv run pytest tests/test_agentic_executor.py tests/test_agentic_workers.py tests/test_dynamic_workflow_receipts.py tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_allowed_policy_runs_producer_without_blocking_on_missing_receipts tests/test_dual_agent_workflow_driver.py::test_agentic_required_blocks_solo_execution_before_lead tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_required_policy_still_blocks_without_executor_receipts tests/test_dual_agent_workflow_driver.py::test_submit_workflow_job_payload_round_trips_agentic_policy_fields -q
```

Result:

```text
28 passed in 1.60s
```

## Full Suite

Command:

```text
uv run --extra dev pytest -q
```

Result:

```text
586 passed in 105.08s (0:01:45)
```

## Hygiene

Commands:

```text
git diff --check
uv run python -m py_compile supervisor/agentic_executor.py supervisor/agentic_workers.py supervisor/dynamic_workflow_receipts.py mcp_tools/codex_supervisor_stdio.py
```

Result:

```text
passed
```

## Live Agentic Smoke

Command:

```text
uv run codex-supervisor-workflow --config /Users/sam.zhang/.codex-supervisor/config.yaml --request docs/dual-agent/agentic-executor-wiring-inline-20260531/agentic-live-smoke-request.json --output docs/dual-agent/agentic-executor-wiring-inline-20260531/agentic-live-smoke-result-rerun1.json --fail-on-blocked
```

Result:

```text
run_id=codex-agentic-executor-live-smoke-20260531-rerun1
dual_agent_agentic_worker_production event 416484: status=passed, receipt_count=1
dual_agent_dynamic_workflow_receipt_validation event 416486: P13 status=green, achieved_evidence_grade=runtime_native
worker receipt: agentic-worker-audit-1
worker refs: .handoff/agentic-workers/agentic-executor-live-smoke-20260531/audit-1/{transcript.jsonl,output.json,worker.log}
```
