# Implementation Plan

## Files / Modules To Touch

- `supervisor/dynamic_workflow.py`
- `supervisor/dynamic_workflow_receipts.py`
- `supervisor/dual_agent_lead.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/agent_invoker.py`
- `supervisor/state.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `tests/test_dynamic_workflow_receipts.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `tests/test_dual_agent_runner.py`
- `tests/test_agent_invoker_review.py`
- `docs/dual-agent/dynamic-workflow-fanout-and-transport-recovery-20260530/source/*.md`
- `docs/testing/prd-to-tdd-translation-audit-dynamic-workflow-fanout-and-transport-recovery-20260530.md`

## Risks

- Replay verification can become too strict and reject old shape-only receipts. Mitigation: strict replay checks are enforced on public MCP paths that pass `cwd`, while legacy helper calls without `cwd` remain shape-compatible.
- The supervisor-generated manifest can collide with caller-provided dynamic manifests. Mitigation: write the supervisor manifest to `supervisor-dynamic-workflow-manifest.json`.
- Resume can skip gates incorrectly if it trusts stale state. Mitigation: skip only contiguous prior accepted route steps and keep pending gates on the normal artifact/receipt path.
- Lock reclaim can steal an active lock if pid reuse is mishandled. Mitigation: live pids block; dead pids and stale pidless locks are the reclaimable cases.
- Automatic receipts can become another model claim. Mitigation: synthesize only from machine-readable subagent result receipts and still run P13 hash verification.
- Detached workflow jobs can drift from the inline MCP path. Mitigation: invoke `mcp_tools.codex_supervisor_workflow_cli`, which already calls the same `CodexSupervisorMcpAPI.run_dual_agent_workflow` boundary and writes the same ledger/artifacts.

## Traceability

- P6 -> test_run_dual_agent_workflow_blocks_dynamic_preview_with_forged_replay_refs
- P6 -> test_verify_dynamic_workflow_receipts_recomputes_transcript_output_and_replay_hashes
- P6 -> test_verify_dynamic_workflow_receipts_requires_timeout_and_budget
- P6 -> test_verify_dynamic_workflow_receipts_requires_changed_file_manifest
- P7 -> test_dynamic_reviewer_synthesis_blocks_on_critical_disagreement
- P8 -> test_run_dual_agent_workflow_generates_dynamic_manifest_and_auto_receipts
- P9 -> test_cs24_gate_runner_reclaims_dead_handoff_lock_without_invoking_user
- P10 -> test_run_dual_agent_workflow_resumes_after_transport_loss_from_pending_gate
- P11 -> test_agent_invoker_imports_without_claude_agent_sdk
- P12 -> test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job
- P12 -> test_poll_dual_agent_workflow_job_reads_durable_result_after_transport_loss

## Steps

1. Add RED tests at `codex_supervisor_mcp`, `dual_agent_runner`, and `agent_invoker` boundaries.
2. Add `supervisor.dynamic_workflow` for registry, manifest, automatic receipt synthesis, replay hash helpers, and N-agent synthesis.
3. Extend P13 to accept `cwd`, verify referenced hashes, require timeout and budget, and enforce changed-file manifests.
4. Wire receipt synthesis and manifest/synthesis ledger events into `run_dual_agent_workflow` before P13 validation.
5. Update direct gate P13 validation to pass `cwd`.
6. Add stale handoff lock reclaim in `supervisor.dual_agent_runner`.
7. Add accepted-step resume in `run_dual_agent_workflow`.
8. Lazy-load `claude_agent_sdk` in `supervisor.agent_invoker`.
9. Add durable workflow job persistence plus `submit_dual_agent_workflow_job` / `poll_dual_agent_workflow_job`.
10. Run focused tests, then the full `uv run --extra dev pytest -q` suite.

## Tradeoffs

- This slice validates dynamic subagent outputs but does not launch real subagents from the Python supervisor package.
- Same-API resume plus durable submit/poll jobs improve transport recovery for operators, but automatic Codex Desktop reconnection remains a later lower-level transport slice.
- Strict hash verification may block previously accepted shape-only receipts; that is intentional because forged receipts were the failure mode.
