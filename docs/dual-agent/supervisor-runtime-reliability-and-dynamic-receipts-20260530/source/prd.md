# PRD: Supervisor Runtime Reliability and Dynamic Workflow Receipts

## Problem Statement

Transport and runtime failures currently force too many manual restarts because a long-running supervisor subsystem can fail in a way that either exits the daemon or loses operator-visible health evidence. At the same time, dynamic workflow preview has policy text in the handoff packet but no deterministic receipt gate proving that native fan-out stayed under Codex plus Claude Code supervision.

## Solution

Add a reliability base layer that records subsystem health and restarts restartable daemon loops with bounded backoff, while keeping fatal hook-server failures visible. Harden rollout ingestion so progress callback failures cannot kill tailing or duplicate already-ingested rollout lines. Serialize parent-side ledger writes through `State.write_event`. Add P13 as an LLM-free dynamic workflow receipt validator and wire it into the Codex supervisor MCP workflow and direct gate surfaces.

## User Stories

- As an operator, I want the supervisor to keep observing sessions when a watcher or progress callback fails, so I do not have to restart the whole process for recoverable runtime errors.
- As an operator, I want every recoverable subsystem failure written into the event ledger, so I can distinguish degraded supervision from a clean run.
- As a workflow owner, I want dynamic workflow preview to block without machine-readable receipts, so fan-out cannot replace Codex review or Claude Code `/lead` integration by accident.
- As a reviewer, I want P13 validation visible in transcripts and artifacts, so I can audit exactly why dynamic workflow preview was accepted or blocked.

## PRD Promise Contracts

P1. Runtime Subsystem Survival

- User-visible promise: A restartable supervisor subsystem failure does not kill the whole daemon or silently disappear.
- Representative action: A watcher, poller, detector, invoker, or monitor loop raises at runtime after daemon startup.
- Public boundary: `supervisor_runtime_loop`.
- Allowed outcomes: The failing subsystem emits a `supervisor_subsystem_health` event and is restarted with bounded backoff; fatal hook-server startup/runtime failures emit a failed health event and still stop the daemon.
- Forbidden outcomes: One restartable task exception cancels every other subsystem; runtime failures are only in stderr logs; hook-server bind failures are hidden behind an infinite restart loop.

P2. Rollout Ingestion Callback Survival

- User-visible promise: Rollout ingestion keeps tailing even if notification/progress callbacks fail.
- Representative action: A rollout line is valid, is written to the ledger, then the `on_event` callback raises.
- Public boundary: `event_ingestion_api`.
- Allowed outcomes: The rollout event and tail offset commit together for the parsed line, a health event records callback/read/parse failures, malformed complete lines advance with health evidence, and later lines can still be processed.
- Forbidden outcomes: Callback failure aborts the watcher loop, loses the already-ingested event, or leaves the offset in a state that duplicates completed lines on restart.

P3. Trace-Safe Ledger Writes

- User-visible promise: Parallel reviewer/dynamic-workflow parent writes cannot corrupt the SQLite event stream.
- Representative action: Multiple parent-side workers append dual-agent or health events concurrently.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: `State.write_event` serializes writes through one process-local lock, stamps trace envelopes, redacts payloads, and returns unique event ids.
- Forbidden outcomes: Concurrent writes interleave on one SQLite connection, throw transient transaction errors, or bypass trace/redaction.

P4. Dynamic Workflow Receipt Gate

- User-visible promise: Dynamic workflow preview cannot be treated as accepted merely because Claude returns an accepting outcome.
- Representative action: `run_dual_agent_workflow` or `start_dual_agent_gate` is called with `execution_layer_mode=dynamic_workflow_preview`.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: The workflow or direct gate blocks before Claude invocation unless tool receipts prove the existing preview gates; passing receipts are written to the ledger and exported in transcripts/artifacts.
- Forbidden outcomes: Dynamic fan-out runs with no subagent budget, tool/permission, machine-readable output, headless, replay/CI, supervision-layer, or throwaway-comparison receipts.

P5. PRD-to-TDD Traceability Preserved

- User-visible promise: Reliability and dynamic workflow changes retain the full PRD -> grill -> issues -> TDD -> grill trail.
- Representative action: An operator inspects the exported dual-agent artifact pack or source docs after the implementation.
- Public boundary: `codex_supervisor_mcp`.
- Allowed outcomes: Source PRD, PRD grill findings, issues, TDD plan, TDD grill findings, and implementation plan exist; workflow route and validation events are ledger-visible.
- Forbidden outcomes: Reliability fixes land as untracked patches, dynamic workflow gates are prompt-only, or new events are written but absent from transcript/artifact read paths.

## Implementation Decisions

- Restart only daemon subsystems that are long-running and recoverable; keep `hook_server` fatal so bind/config failures remain loud.
- Validate dynamic workflow preview at workflow start and at direct gate entry because both are public MCP surfaces.
- Reuse the existing `DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES` list as P13's required gate list instead of inventing a second policy vocabulary.
- Keep P13 deterministic and receipt-based; do not let a model reviewer attest that dynamic workflow receipts exist.

## Testing Decisions

- First RED tests hit public boundaries: `supervisor_runtime_loop`, `event_ingestion_api`, `supervisor_event_ledger`, and `codex_supervisor_mcp`.
- Runtime and MCP tests fake Claude, Cursor, Telegram, filesystem watches, and live model calls.
- Tests assert both blocked and accepted dynamic workflow preview behavior, plus transcript and artifact visibility.
- The full repo test command is attempted, but collection currently requires `claude_agent_sdk`; affected modules are run through `uv run --extra dev`.

## Out of Scope

- Full N-reviewer registry and severity-aware reviewer synthesis.
- Mutating dynamic-workflow fan-out or automatic fan-in/merge.
- Cross-process distributed ledger sequencing beyond SQLite WAL and process-local `State.write_event` serialization.
- Retrying failed progress notifications after a callback exception.
