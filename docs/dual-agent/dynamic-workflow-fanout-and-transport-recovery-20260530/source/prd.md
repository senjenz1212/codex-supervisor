# PRD: Dynamic Workflow Fan-out, Replay Receipts, and Resume Recovery

## Problem Statement

Dynamic workflow preview currently blocks unless callers supply P13 receipts, but those receipts are shape-checked rather than replay-verified. The workflow also has no deterministic reviewer registry, no N-agent synthesis model, no automatic receipt generation from subagent outputs, and no durable resume path that can recover after a long MCP call loses transport.

## Solution

Add a supervisor-owned dynamic workflow policy module that defines task-class reviewer registries, deterministic fan-out manifests, automatic receipt synthesis from subagent result receipts, and N-agent synthesis rules. Harden P13 so preview receipts must point to durable files whose hashes match. Add stale handoff lock reclaim and make workflow reruns resume from the first non-accepted gate, so a client transport failure can restart through the same public API without repeating already accepted gates. Add a durable submit/poll job path that launches long workflows in a detached CLI worker, so the MCP call that starts the work returns quickly and polling can resume after transport loss. Make `AgentInvoker` importable without the optional Claude Agent SDK.

## User Stories

1. As an operator, I want dynamic workflow preview receipts to be replay-verified, so that forged strings cannot pass as evidence.
2. As a workflow owner, I want a registry-selected fan-out manifest, so that subagents have bounded roles, tools, budgets, and permissions.
3. As a reviewer, I want critical subagent objections to block deterministically, so that N-agent review increases rigor instead of hiding disagreement.
4. As an operator recovering from `Transport closed`, I want to rerun the same workflow safely, so that already accepted gates are skipped and traceability is preserved.
5. As a maintainer, I want optional Claude SDK imports to stay optional during collection, so the dev suite can run without the `agent` extra.
6. As an operator running a long workflow, I want to submit it as a durable job and poll for results, so the work can continue even if the initiating MCP call closes.

## PRD Promise Contracts

P6. Replay-Verified Dynamic Receipts

- User-visible promise: Dynamic workflow preview cannot pass on forged transcript/output/replay strings.
- Representative action: `run_dual_agent_workflow` is called with `execution_layer_mode=dynamic_workflow_preview` and receipt refs/hashes.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: P13 opens referenced files under the workspace, recomputes sha256 values, records verified refs in the ledger, and blocks before Claude when any ref is missing or mismatched.
- Forbidden outcomes: A receipt with fake `transcript_ref`, `output_hash`, `replay_ref`, or `worktree_comparison_ref` passes because the strings are non-empty.

P7. Reviewer Registry And N-Agent Synthesis

- User-visible promise: Dynamic fan-out has deterministic reviewer roles and deterministic fan-in, not prompt-only reviewer prose.
- Representative action: A workflow asks for `dynamic_workflow_task_class=codebase_audit` or `cortex_pod_four_reviewer_fanout`.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: The workflow route records a manifest with registry-selected subagents/reviewers, expected gates, budget, timeout, permission mode, tool pins, and synthesis policy. Critical or important disagreement blocks the workflow before the final gate can be treated as accepted.
- Forbidden outcomes: Arbitrary unregistered personas decide acceptance, or a critical subagent objection is hidden behind a Claude accept outcome.

P8. Automatic Dynamic Receipts

- User-visible promise: Subagent outputs become machine-readable receipts automatically instead of requiring the operator to hand-author every P13 gate receipt.
- Representative action: The caller supplies dynamic subagent result receipts with transcript/output refs and decisions.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: The supervisor synthesizes a dynamic manifest receipt and all required preview-gate receipts from the subagent results, persists them in validation events, and validates them through P13.
- Forbidden outcomes: Subagent evidence is ignored unless the caller manually duplicates it into every preview-gate receipt.

P9. Stale Lock Reclaim

- User-visible promise: A dead process cannot permanently block future dual-agent gates with an orphaned `.handoff/.dual-agent.lock`.
- Representative action: A lock file names a dead pid or is older than the reclaim TTL.
- Public boundary: `dual_agent_runner`.
- Allowed outcomes: The runner reclaims stale/dead locks, writes a new lock, and invokes the gate. Active live locks still block.
- Forbidden outcomes: A stale lock forces manual cleanup; an active lock is stolen.

P10. Transport Resume Without Repeating Accepted Gates

- User-visible promise: If a long MCP workflow call loses transport after some gates accepted, re-running the workflow resumes from the first non-accepted gate and keeps traceability.
- Representative action: `run_dual_agent_workflow` is called again with the same `run_id` and `task_id` after existing accepted workflow steps.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: The workflow reads prior step state, skips already accepted gates in the current route, records the resumed route/steps, and continues with the first pending gate.
- Forbidden outcomes: Recovery starts from scratch, repeats already accepted gates, loses existing transcript refs, or reports full success without the missing gate.

P11. Optional Claude SDK Import Health

- User-visible promise: The full dev suite can collect without installing the optional Claude Agent SDK.
- Representative action: `uv run --extra dev pytest -q` imports `supervisor.agent_invoker`.
- Public boundary: `agent_invoker`.
- Allowed outcomes: Import and non-SDK tests succeed; actual runtime invocation raises a clear dependency error if the SDK is missing.
- Forbidden outcomes: Test collection fails at module import because optional `claude_agent_sdk` is absent.

P12. Durable MCP Workflow Job Recovery

- User-visible promise: Long dual-agent workflows can be launched without keeping one MCP tool call open for the full Claude `/lead` run.
- Representative action: `submit_dual_agent_workflow_job` is called with the same payload as `run_dual_agent_workflow`, then `poll_dual_agent_workflow_job` is called after the original MCP transport is gone or restarted.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: The submit tool writes a durable request/result/log path, starts a detached `codex-supervisor-workflow` worker against the same supervisor API, records job metadata in SQLite and the event ledger, returns immediately with a job id, and polling reads the durable result plus resume state.
- Forbidden outcomes: The only recovery path requires a long MCP call to stay open; the detached worker is untraceable; polling cannot recover a completed result after the original transport closes.

## Implementation Decisions

- Dynamic workflows stay an execution-layer preview under Codex plus Claude Code `/lead`; ADR 0003 remains the control boundary.
- P13 remains deterministic and LLM-free.
- Hash refs use `sha256:<hex>` or plain `<hex>` and are resolved relative to the workflow `cwd`.
- Registry and synthesis are data-driven and deterministic. This slice does not launch live Codex/Claude subagents itself; it validates and fan-ins subagent result receipts produced by the agentic layer.
- MCP transport recovery has two layers: same-API workflow resume for reruns, and durable submit/poll jobs for long calls. Lower-level automatic client/server reconnect remains a separate runtime integration concern.

## Testing Decisions

- First RED tests for P6, P7, P8, and P10 use `codex_supervisor_mcp` through `run_dual_agent_workflow`.
- The P9 stale-lock RED test uses `dual_agent_runner` because lock ownership is runner state.
- The P11 SDK import RED test uses `agent_invoker` and blocks `claude_agent_sdk` import explicitly.
- The P12 job recovery tests use `codex_supervisor_mcp` with a fake detached worker process and durable result file.
- All dynamic receipt tests use local files and sha256 fixtures; no live Claude, Cursor, Telegram, Codex Desktop, or network calls are allowed.
- The full dev-suite proof is `uv run --extra dev pytest -q`.

## Out of Scope

- Launching real remote Claude Code or Codex subagents from this Python package.
- Distributed cross-process locking beyond local pid/TTL reclaim.
- Automatic Codex Desktop reconnection to a killed MCP server process; this slice gives operators a durable job id and result path instead.
- Promoting dynamic workflow preview to the default execution path.
