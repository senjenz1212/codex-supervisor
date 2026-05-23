# Public Boundaries

Tests for PRD promises must start at one of these boundaries.

## target_config_load

Load a supervisor config file and construct the selected target adapter without
starting the daemon.

## target_adapter_conformance

Run the shared adapter conformance suite against a target adapter implementation
or fake adapter.

## hook_http_api

Call the FastAPI hook endpoint with a raw target hook payload and inspect the
HTTP response plus persisted state.

## mode_policy

Evaluate a capability decision with a frozen mode snapshot and verify the action
or would-do result.

## run_registration_api

Register or discover a run and verify the immutable run snapshot and scope
contract written for that run.

## event_ingestion_api

Ingest raw target events through an adapter or fixture tailer and inspect the
normalized event stream and persisted offsets.

## redaction_pipeline

Pass raw event, hook, verdict, action, and Telegram payload text through the
storage and notification paths and verify secrets are absent.

## replay_cli

Invoke the replay entrypoint on a fixture run and compare normalized events,
scope findings, verdicts, and proposed actions with expected output.

## action_executor

Submit a target action with fake Telegram and fake target adapter dependencies
and verify approval, expiry, deduplication, execution, and failure behavior.
When `execute_actions_async` is used for aggressive/enforce steering, tests
must prove only explicitly allowlisted non-destructive actions auto-execute and
destructive actions still require approval.

## telegram_chat_ingress

Submit a Telegram text message or callback update through the Telegram poller
entrypoint and verify the persisted supervisor turn, Claude supervisor
invocation, Telegram response, and resulting proposed actions.

## supervisor_tool_api

Invoke the local supervisor tools available to the Claude supervisor runtime
with fake state/target/slack dependencies and verify returned data, permission
checks, redaction, and mode-aware mutation behavior.

## named_session_resolver

Resolve a human-facing target session name such as "Vela chat bot" to the
current Codex session id and rollout path using local session metadata,
registry records, rollout content, and explicit aliases. Verify ambiguity,
not-found, and stale-session behavior.

## supervisor_turn_replay

Replay a Telegram supervisor conversation turn from frozen inputs, tool outputs,
and model output fixtures, and verify the same response/action labels without
live Telegram, Slack, target agents, or model APIs.

## agent_invoker

Submit a queued supervisor decision to the bounded Claude Agent SDK invoker with
fake MCP servers and verify the selected skill, model, allowed tools, persisted
verdict, and Telegram-facing result. Tests at this boundary may fake the SDK
below the invoker, but must not skip skill selection or tool allowlisting.

## codex_app_server_status_sync

Submit `TargetAction(kind="append_status_item")` through `CodexAdapter` and
verify it speaks Codex app-server JSON-RPC `initialize` +
`thread/inject_items` to a fake stdio server. Tests at this boundary may fake
the app-server subprocess, but must not bypass `CodexAdapter.execute_action`.

## telegram_progress_context

Stream a watched rollout event through `TelegramProgressStreamer`, then submit
a follow-up Telegram message through `TelegramChatSupervisor` and verify the
runtime receives the prior outbound progress notification in conversation
context. Tests may fake Telegram delivery and the model runtime, but must not
skip watch state, progress send, or supervisor turn persistence.

## telegram_mcp_tools

Invoke the Telegram MCP toolpack used by Claude decision agents and verify
message routing, urgency policy, FYI suppression, and approval prompts through
the public tool functions. Tests may fake the SDK decorator and Telegram
notifier, but must not bypass `build_telegram_mcp_server`.

## progress_context_backfill

Repair an already-sent watched-run progress notification by loading the stored
event from `State`, formatting the same progress message as live streaming, and
persisting it as read-only supervisor conversation context. Tests must verify
idempotence, non-empty chat id enforcement, redaction, and no Telegram send.

## desktop_progress_status_sync

Stream a watched rollout event through `TelegramProgressStreamer` with Desktop
status sync enabled and verify the target adapter receives
`TargetAction(kind="append_status_item")` for the run's session id. Tests must
also prove `off` mode does not call the adapter and adapter failure is audited
without blocking Telegram progress.

## desktop_status_visibility_policy

Classify the adapter result from a passive Desktop status append into an
operator-facing visibility state. Tests must prove unverified GUI repaint maps
to `history_only`, verified repaint maps to `gui_live`, and health/status
surfaces do not equate transport delivery with GUI visibility.

## codex_desktop_gui_steering

Submit `TargetAction(kind="inject_steering")` through `CodexAdapter` with
`steering_delivery=desktop_gui` and verify the approved steering message is
routed through the explicit Desktop GUI controller rather than invisible CLI
resume delivery. Tests may fake the GUI controller below the adapter, but must
not bypass `CodexAdapter.execute_action`, approval-shaped `TargetAction`
payloads, redaction, or failure metadata.

## codex_desktop_ipc_client

Connect to Codex Desktop's local Electron IPC socket through
`CodexDesktopIpcClient` and verify framed initialize, diagnostic request, and
safe broadcast behavior. Tests may fake the Unix socket below the client, but
must not use newline JSON-RPC framing, start turns, steer turns, click the GUI,
or claim live Desktop repaint from a broadcast alone.

## codex_desktop_ipc_stream_decode

Replay or capture framed Codex Desktop IPC messages and decode
`thread-stream-state-changed` snapshot/patch broadcasts. Tests may fake the
socket below `CodexDesktopIpcClient`, but must not start turns, steer turns,
submit GUI input, or treat decoded stream messages as verified visible repaint
without an independent renderer-observed or human-visible signal.

## codex_desktop_ipc_role_discovery

Replay or capture Codex Desktop IPC `client-discovery-request` frames and verify
the supervisor records method/version/parameter keys without parameter values.
Tests may configure explicit request handlers below `CodexDesktopIpcClient`,
but must prove `canHandle=true` is never advertised without a handler and that
handled forwarded requests receive a response instead of hanging the router.

## codex_desktop_ipc_turn_steer_route_probe

Probe `turn/steer` routing semantics through a fake framed IPC router and
`CodexDesktopIpcClient`. Tests must prove mutating methods are blocked by
default, fixture probes require explicit opt-in, and any advertised
`turn/steer` handler returns a response instead of hanging the router. This
boundary must not connect to the real Desktop IPC socket.

## codex_desktop_ipc_capture_diff

Replay cold-start and normal-turn Codex Desktop IPC JSONL captures and diff
only method names, message types, thread stream change types, and patch paths.
Tests must prove raw params, prompt text, patch values, and secrets are not
stored in summaries; fixture analysis must not connect to the real Desktop IPC
socket, start turns, steer turns, submit GUI input, or mark GUI repaint as
verified.

## dual_agent_slice0

Submit fixture-shaped probe evidence through `supervisor.dual_agent` validators
and verify Slice 0 hard-stop, lightweight artifact-exposure guardrails,
budget-pause, worker-orchestration fidelity, Telegram batching,
parallel-isolation, and claim-verification behavior. Tests must not call live
Claude, Codex, Telegram, ChatGPT mobile, Cortex, or Codex Desktop by default.
Live probes must first be adapted into the same fixture shapes before they can
unblock CS24. The exposure guardrail should catch obvious raw credential
publication to operator-facing surfaces without making exhaustive secret
classification the center of the Slice 0 effort.

## dual_agent_lead_invocation

Build a Claude Code `/lead` invocation through `supervisor.dual_agent_lead` and
adapt the captured transcript into `supervisor.dual_agent` outcome validators.
Tests must inject a fake runner rather than spawning live Claude. The command
builder must use non-bare `claude -p` so slash commands can resolve, must expose
model-quality policy explicitly, must capture stdout/stderr without truncation,
and must fail closed when the process fails or the typed outcome loses expected
specialist decisions or objections. The handoff writer must create
`.handoff/<task_id>.json` with an explicit packet schema version, immutable
planning artifact checksums, pinned `/lead` skill version/hash, and the selected
outcome-validation failure policy. Post-worker review must be able to verify
that immutable planning artifacts still match the checksums from handoff.

## dual_agent_runner

Run dual-agent gates through `supervisor.dual_agent_runner`. Tests must inject
fake Claude runners and fake Telegram notifiers. The runner must write the
typed handoff packet, invoke the `/lead` boundary, classify P1/P2/P3 probe
evidence, retry malformed outcome blocks at most once when policy allows, stop
the gate sequence on the first blocked gate, and never choose a winner on
budget exhaustion. The runner must acquire a worktree-level
`.handoff/.dual-agent.lock` before writing the handoff packet or invoking
`/lead`, refuse a second concurrent gate in the same worktree without invoking
Claude, and remove the lock after accepted or blocked worker outcomes.
Deadlock escalation must create a `dual_agent_gate_deadlock`
action, create a nonce-protected Telegram ask with `Pause`, `Kill`, and
`Continue`, and resolve callbacks through the existing Telegram poller without
executing destructive work directly. Validation failures controlled by the
packet policy must create `dual_agent_validation_failure` actions and Telegram
asks instead of silently blocking. `Continue` answers must be claimable exactly
once before re-dispatching the matching gate. Paused dual-agent actions must
emit at most one stale digest after the stale threshold and remain paused.

## codex_supervisor_mcp

Codex consumes supervisor control through the stdio MCP entrypoint
`codex-supervisor-mcp`, implemented by `mcp_tools.codex_supervisor_stdio`.
Tests must verify the server exposes the dual-agent gate tools Codex needs:
`start_dual_agent_gate`, `record_gate_round`, `check_budget`,
`escalate_deadlock`, `poll_resume_signal`, `read_outcome`,
`read_gate_transcript`, and `start_codex_session`. The MCP boundary must
persist gate results and round decisions to the supervisor event ledger so
later tools can read outcomes and reconstruct the dialogue without relying on
chat memory. `start_codex_session` must default to the strongest configured
Codex model and high reasoning (`gpt-5.5` with
`reasoning_effort="xhigh"`) unless the caller explicitly overrides it. In the
Codex Desktop initiated no-Telegram scope, a blocked gate
must escalate through Desktop chat and re-run `start_dual_agent_gate`; it must
not wait on `poll_resume_signal` unless a Telegram callback exists.
