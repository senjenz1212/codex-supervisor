# Issues

## Slice ISS-1: Replay-Verified P13 Receipts

Type: AFK
Priority: P0
Estimate: M
Scope: Extend P13 to resolve receipt refs under `cwd`, recompute sha256 values, require both timeout and budget, and require changed-file manifests.
PRD promise: P6.
First public-boundary RED test: `codex_supervisor_mcp` forged replay refs block before Claude.

Acceptance Criteria:
- [ ] Forged dynamic preview receipts block at workflow start with zero Claude runner calls.
- [ ] Valid local transcript/output/replay/comparison refs pass after sha256 recomputation.
- [ ] Missing budget, timeout, or `changed_files` fields fail P13.

Tests:

- `test_run_dual_agent_workflow_blocks_dynamic_preview_with_forged_replay_refs`
- `test_verify_dynamic_workflow_receipts_recomputes_transcript_output_and_replay_hashes`

Allowed outcomes: refs resolve under cwd and hashes match.

Forbidden outcomes: non-empty fake refs pass.

## Slice ISS-2: Registry Manifest And N-Agent Synthesis

Type: AFK
Priority: P0
Estimate: M
Scope: Add a registry-backed manifest builder, automatic dynamic receipt synthesis from subagent result receipts, and deterministic synthesis blocking.
PRD promise: P7, P8.
First public-boundary RED test: `codex_supervisor_mcp` dynamic subagent result receipts generate manifest and P13 receipts.

Acceptance Criteria:
- [ ] Workflow route records registry-selected manifest, manifest ref, manifest hash, and synthesis status.
- [ ] Subagent result receipts synthesize every required P13 preview-gate receipt.
- [ ] Critical or important unresolved subagent objections block before Claude execution.

Tests:

- `test_run_dual_agent_workflow_generates_dynamic_manifest_and_auto_receipts`
- `test_dynamic_reviewer_synthesis_blocks_on_critical_disagreement`

Allowed outcomes: route/transcript exposes registry manifest, synthesized receipts, and deterministic fan-in.

Forbidden outcomes: unregistered reviewer results or critical disagreements are ignored.

## Slice ISS-3: Stale Handoff Lock Reclaim

Type: AFK
Priority: P1
Estimate: S
Scope: Reclaim dead or stale `.handoff/.dual-agent.lock` files while preserving live locks.
PRD promise: P9.
First public-boundary RED test: `dual_agent_runner` reclaims a dead-pid lock and invokes the fake runner.

Acceptance Criteria:
- [ ] Dead-pid locks are reclaimed and cleaned up after the gate.
- [ ] Fresh locks owned by a live pid still block.
- [ ] Malformed locks block conservatively unless stale TTL evidence is available.

Tests:

- `test_cs24_gate_runner_reclaims_dead_handoff_lock_without_invoking_user`
- keep `test_cs24_gate_runner_refuses_existing_handoff_lock_without_invoking_lead`

Allowed outcomes: dead/stale locks are reclaimed; live locks still block.

Forbidden outcomes: stale locks require manual deletion or active locks are stolen.

## Slice ISS-4: Same-API Workflow Resume

Type: AFK
Priority: P1
Estimate: M
Scope: Teach `run_dual_agent_workflow` to inspect existing accepted workflow steps and continue from the first pending route gate.
PRD promise: P10.
First public-boundary RED test: `codex_supervisor_mcp` rerun skips prior accepted gates after simulated transport loss.

Acceptance Criteria:
- [ ] Existing accepted route steps are returned in the result and not reinvoked.
- [ ] Pending gates still run through normal artifact, receipt, claim, and review checks.
- [ ] The workflow route exposes a resume block with skipped and pending gates.

Tests:

- `test_run_dual_agent_workflow_resumes_after_transport_loss_from_pending_gate`

Allowed outcomes: already accepted gates are skipped and pending gates continue.

Forbidden outcomes: rerun starts from scratch or claims success without the pending gate.

## Slice ISS-5: Optional SDK Import Health

Type: AFK
Priority: P1
Estimate: S
Scope: Lazy-load Claude Agent SDK classes inside `AgentInvoker` runtime invocation.
PRD promise: P11.
First public-boundary RED test: `agent_invoker` imports while `claude_agent_sdk` is unavailable.

Acceptance Criteria:
- [ ] Importing `supervisor.agent_invoker` does not require `claude_agent_sdk`.
- [ ] Existing fake-SDK review tests still verify tool allowlisting and verdict writes.
- [ ] Actual SDK invocation raises a clear optional dependency error when the SDK is absent.

Tests:

- `test_agent_invoker_imports_without_claude_agent_sdk`
- existing `test_review_updates_invoker_uses_read_only_grounding_tools`

Allowed outcomes: import succeeds without SDK; runtime raises a clear dependency error only if invocation needs the SDK.

Forbidden outcomes: pytest collection fails before tests can run.

## Slice ISS-6: Durable MCP Workflow Jobs

Type: AFK
Priority: P0
Estimate: M
Scope: Add `submit_dual_agent_workflow_job` and `poll_dual_agent_workflow_job` so long workflows run in a detached CLI worker instead of a single long MCP tool call.
PRD promise: P12.
First public-boundary RED test: `codex_supervisor_mcp` submits a workflow job and returns immediately with durable request/result/log refs.

Acceptance Criteria:
- [ ] Submit records job metadata in SQLite and the event ledger.
- [ ] Submit starts `mcp_tools.codex_supervisor_workflow_cli` in a detached process using the same workflow payload.
- [ ] Poll reads a durable result file and returns the workflow verdict plus resume state.
- [ ] Transcript reads expose workflow job events for traceability.

Tests:

- `test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job`
- `test_poll_dual_agent_workflow_job_reads_durable_result_after_transport_loss`

Allowed outcomes: long workflow execution can survive loss of the initiating MCP call.

Forbidden outcomes: operator must keep a long MCP call open to recover the result.
