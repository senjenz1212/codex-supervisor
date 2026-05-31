# TDD Plan

## Public Boundary Registry

- `codex_supervisor_mcp`: first proof for dynamic manifest, receipt replay, synthesis, and workflow resume.
- `dual_agent_runner`: first proof for stale handoff lock reclaim.
- `agent_invoker`: first proof for optional Claude SDK import health.

## Cycle 1: Replay-Verified P13

RED: Add a workflow-level test where dynamic preview receives forged refs and hashes. It must block at `workflow_start`, make zero Claude runner calls, and record P13 details.

GREEN: Extend `verify_dynamic_workflow_receipts` to resolve refs under cwd and verify `transcript_sha256`, `output_sha256`, `replay_sha256`, and `worktree_comparison_sha256`.

## Cycle 2: Automatic Receipts And Registry Manifest

RED: Add a workflow test with only `dynamic_subagent_result` receipts. It must generate a deterministic dynamic manifest and required preview-gate receipts, then accept with the fake runner.

GREEN: Add a data-driven dynamic workflow registry, manifest builder, subagent result normalizer, receipt synthesizer, and route/transcript event exposure.

## Cycle 3: N-Agent Synthesis Blocking

RED: Add a workflow test where one registered subagent result carries a critical objection. It must block before Claude and expose the synthesis blocker.

GREEN: Add deterministic synthesis that blocks on critical/important unresolved objections and records `dynamic_workflow_synthesis`.

## Cycle 4: Stale Lock Reclaim

RED: Add a runner test with a lock containing a dead pid or expired timestamp. The gate should reclaim and run. Preserve the existing live-lock block test.

GREEN: Read lock metadata, check pid liveness and TTL, unlink reclaimable locks, and report active-lock metadata when blocking.

## Cycle 5: Workflow Resume

RED: Add a same-API workflow rerun test with prior accepted steps through `tdd_review`. The next call should run only `implementation_plan`, `execution`, and `outcome_review`.

GREEN: Teach `run_dual_agent_workflow` to inspect existing accepted steps for the same route and start from the first pending gate.

## Cycle 6: Optional SDK Import

RED: Simulate missing `claude_agent_sdk` and import/reload `supervisor.agent_invoker`.

GREEN: Lazy-load SDK classes inside `_handle` and raise a clear optional dependency error only when unavailable at invocation time.

## Cycle 7: Durable MCP Job Submit/Poll

RED: Add an MCP boundary test where `submit_dual_agent_workflow_job` starts a detached worker and returns a job id without executing the workflow inline. Add a poll test that reads a durable result file after the initiating transport is gone.

GREEN: Persist workflow job metadata, write request/result/log paths, spawn `mcp_tools.codex_supervisor_workflow_cli` as a detached worker, expose job events in transcripts, and make polling return the durable result plus resume state.

## Test Cases

### test_run_dual_agent_workflow_blocks_dynamic_preview_with_forged_replay_refs

Maps to: ISS-1, P6.
RED: Call `run_dual_agent_workflow` with dynamic preview and shape-only fake refs. Assert `workflow_start` blocks, P13 is red, and the fake Claude runner has zero calls.
GREEN: Pass `cwd` into P13 and require referenced file hashes before any Claude invocation.

### test_verify_dynamic_workflow_receipts_recomputes_transcript_output_and_replay_hashes

Maps to: ISS-1, P6.
RED: Create local transcript/output/replay/comparison files, verify green, tamper one file, and verify P13 red.
GREEN: Resolve refs under cwd and recompute sha256 for every receipt.

### test_verify_dynamic_workflow_receipts_requires_timeout_and_budget

Maps to: ISS-1, P6.
RED: Remove `budget_usd` while leaving `timeout_s`; P13 must reject the receipt.
GREEN: Require both timeout and budget for every subagent.

### test_verify_dynamic_workflow_receipts_requires_changed_file_manifest

Maps to: ISS-1, P6.
RED: Remove `changed_files`; P13 must reject the receipt.
GREEN: Require an explicit changed-file manifest, even when empty.

### test_run_dual_agent_workflow_generates_dynamic_manifest_and_auto_receipts

Maps to: ISS-2, P7, P8.
RED: Supply only `dynamic_subagent_result` receipts and assert the workflow accepts only after the supervisor synthesizes manifest-backed P13 receipts.
GREEN: Add `supervisor.dynamic_workflow` registry, manifest writer, synthesis, and receipt generation.

### test_dynamic_reviewer_synthesis_blocks_on_critical_disagreement

Maps to: ISS-2, P7.
RED: A subagent result with `decision=block` and `severity=critical` must block at `workflow_start` before Claude.
GREEN: Add deterministic synthesis blocking for registered critical or important objections.

### test_cs24_gate_runner_reclaims_dead_handoff_lock_without_invoking_user

Maps to: ISS-3, P9.
RED: A lock with a dead pid should not block the fake gate runner.
GREEN: Read lock metadata, check pid liveness, reclaim dead locks, and preserve live locks.

### test_run_dual_agent_workflow_resumes_after_transport_loss_from_pending_gate

Maps to: ISS-4, P10.
RED: Existing accepted workflow steps through `tdd_review` must cause rerun to invoke only `implementation_plan`, `execution`, and `outcome_review`.
GREEN: Add route resume state and seed the returned steps with skipped accepted gates.

### test_agent_invoker_imports_without_claude_agent_sdk

Maps to: ISS-5, P11.
RED: Block `claude_agent_sdk` import and import `supervisor.agent_invoker`.
GREEN: Lazy-load SDK classes only inside `_handle`.

### test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job

Maps to: ISS-6, P12.
RED: Submit a long workflow job through the MCP boundary and assert it records request/result/log refs and invokes a detached CLI worker.
GREEN: Add job persistence plus submit tooling around the existing `codex-supervisor-workflow` CLI.

### test_poll_dual_agent_workflow_job_reads_durable_result_after_transport_loss

Maps to: ISS-6, P12.
RED: Seed a completed result file and assert polling returns it after the original call is gone.
GREEN: Poll the job table and result file, update status, and expose workflow job events in transcript reads.

## Regression Commands

- `uv run --extra dev pytest tests/test_dynamic_workflow_receipts.py tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py tests/test_dual_agent_runner.py tests/test_agent_invoker_review.py -q`
- `uv run --extra dev pytest -q`
