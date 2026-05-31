# TDD Plan: Agentic Lead Provenance Foundation

## Public-Boundary Tests

1. `tests/test_dual_agent_workflow_driver.py`
   - `test_agentic_required_blocks_solo_execution_before_lead`
   - Boundary: `codex_supervisor_mcp`
   - Proves `agentic_lead_policy=required` blocks with P13 before `/lead` when no supervisor-verified subagents exist.

2. `tests/test_dynamic_workflow_receipts.py`
   - `test_agentic_evidence_grade_ignores_lead_declared_grade`
   - Boundary: `dual_agent_slice0`
   - Proves declared `runtime_native` is ignored unless replay-verified supervisor-owned refs exist.

3. `tests/test_dynamic_workflow_receipts.py`
   - `test_agentic_required_accepts_supervisor_owned_runtime_native_receipts`
   - Boundary: `dual_agent_slice0`
   - Proves required roles, runtime fields, hashes, timeout/budget, and supervisor-owned refs pass.

4. `tests/test_dynamic_workflow_receipts.py`
   - `test_agentic_required_blocks_critical_independent_reviewer`
   - Boundary: `dual_agent_slice0`
   - Proves critical/important independent reviewer revise/deny blocks.

5. `tests/test_dual_agent_lead_invoker.py`
   - `test_handoff_packet_carries_agentic_lead_policy`
   - Boundary: `dual_agent_lead_invocation`
   - Proves policy fields are in the immutable handoff packet.

6. `tests/test_codex_supervisor_mcp_stdio.py`
   - `test_workflow_cli_payload_preserves_agentic_policy_fields`
   - Boundary: `codex_supervisor_mcp`
   - Proves CLI payloads keep policy fields for durable submit/poll workflow.

7. `tests/test_agentic_workers.py`
   - `test_orphaned_agentic_worker_cleanup_records_timeout_and_log_refs`
   - Boundary: `dual_agent_runner`
   - Proves stale worker cleanup has timeout/budget/log metadata.

8. `tests/test_agentic_eval.py`
   - `test_agentic_eval_report_compares_required_modes`
   - Boundary: `replay_cli`
   - Proves the eval harness emits mode comparison rows and summary.

9. `tests/test_agentic_workers.py`
   - `test_agentic_worker_spawn_captures_supervisor_owned_runtime_native_receipt`
   - Boundary: `dual_agent_runner`
   - Proves a supervisor-spawned worker writes durable stdout/stderr, transcript, output, and log refs whose hashes satisfy `required_evidence_grade=runtime_native`.

10. `tests/test_dual_agent_runner.py`
   - `test_gate_runner_blocks_when_lead_critical_review_requests_revision`
   - Boundary: `dual_agent_gate_runner`
   - Proves a `/lead` outcome cannot advance when its own critical review asks for revision.

11. `tests/test_codex_supervisor_mcp_stdio.py`
   - `test_start_dual_agent_gate_blocks_lead_reported_revision`
   - Boundary: `codex_supervisor_mcp`
   - Proves the public MCP gate surface reports the same blocked status instead of hiding a revise outcome behind an accepted wrapper.

## Existing Tests To Keep Green

- `tests/test_dynamic_workflow_receipts.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_dual_agent_runner.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- full `uv run --extra dev pytest -q`
