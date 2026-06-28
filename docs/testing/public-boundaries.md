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

## supervisor_runtime_loop

Run a long-lived daemon subsystem through the restartable runtime wrapper and
verify failures are written as health events before the subsystem restarts.
Tests must fake the subsystem coroutine; they must not call live Claude, Codex,
Telegram, filesystem watches, or HTTP servers.

## supervisor_event_ledger

Append parent-side events through `State.write_event` and inspect the durable
SQLite event stream. Tests must verify trace-envelope stamping, redaction, and
safe concurrent parent writes through the public append method rather than
writing directly to SQLite.

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

## dual_agent_planning_validator

Validate explicit dual-agent planning artifact paths through
`supervisor.planning_validator` without live Claude, Cursor, Telegram, Browser,
Computer Use, or LLM calls. Tests must prove good/stub/sneaky fixtures for PRD,
issues, TDD plan, grill findings, and implementation plan produce replayable
check IDs; implementation-plan traceability references must resolve to real PRD
promise IDs and TDD test names.

## codex_supervisor_mcp

Codex consumes supervisor control through the stdio MCP entrypoint
`codex-supervisor-mcp`, implemented by `mcp_tools.codex_supervisor_stdio`.
Tests must verify the server exposes the dual-agent gate tools Codex needs:
`start_dual_agent_gate`, `record_gate_round`, `check_budget`,
`escalate_deadlock`, `poll_resume_signal`, `read_outcome`,
`read_gate_transcript`, `run_dual_agent_workflow`,
`submit_dual_agent_workflow_job`, `poll_dual_agent_workflow_job`,
`catch_up_dual_agent_workflow`, and `start_codex_session`. The MCP boundary must
persist gate results and round decisions to the supervisor event ledger so
later tools can read outcomes and reconstruct the dialogue without relying on
chat memory. MCP is a compatibility/native-tool adapter for Codex chat, not the
default long-running orchestration surface. Whole-workflow automation should
prefer AXI JSON (`codex-supervisor-axi --json submit`,
`codex-supervisor-axi --json poll`, and
`codex-supervisor-axi --json catch-up`) over the same durable ledger core; MCP
submit, poll, and catch-up paths must remain non-blocking shims and must not execute workflow phases inline. `start_codex_session` must default to the strongest configured
Codex model and high reasoning (`gpt-5.5` with
`reasoning_effort="xhigh"`) unless the caller explicitly overrides it. In the
Codex Desktop initiated no-Telegram scope, a blocked gate
must escalate through Desktop chat and re-run `start_dual_agent_gate`; it must
not wait on `poll_resume_signal` unless a Telegram callback exists.
Dynamic workflow preview must remain an execution layer under Codex plus Claude
Code supervision: `run_dual_agent_workflow` must write
`dual_agent_dynamic_workflow_receipt_validation` and block with P13 unless
machine-readable receipts cover the preview gates.
Long workflows should use AXI JSON by default when operator transport
reliability matters; MCP `submit_dual_agent_workflow_job` remains available for
compatibility and must return a durable job id quickly. Poll must recover the
result from recorded request/result/log refs without writing request files or
spawning workers.
Reconnect-capable clients should persist their last delivered event id and use
AXI JSON catch-up, or MCP `catch_up_dual_agent_workflow` when staying inside
the native tool path, to replay missed ledger events after a transport drop
before resuming poll.

## agentic_worker_execution

Run supervisor-owned agentic worker execution and cleanup through
`supervisor.agentic_workers`. Tests must verify worker stdout, stderr,
transcript, output, and log refs are written under durable
`.handoff/agentic-workers/<task>/<worker>/` paths; refs are hashed for replay;
runtime metadata includes `agent_runtime`, `agent_id`, `permission_mode`,
`tool_pins`, timeout, and budget; and timeout cleanup preserves the same durable
log refs. Cleanup tests must inject PID liveness and termination functions so
unit tests do not kill real processes.

## agentic_eval_report

Build agentic lead comparison reports through `supervisor.agentic_eval`.
Tests must compare `lead_direct`, `agentic_allowed`, and `agentic_required`
rows across wall-clock, cost, retries, rejected gates, missed issues, and
operator interventions. Reports must never authorize an agentic default change
without an explicit operator review gate.

## reviewer_panel_eval_runner

Replay labeled reviewer-panel fixtures through `supervisor.reviewer_panel_eval`
and emit per-reviewer plus pairwise dependency metrics. Tests must use
deterministic fixture or cassette replay by default, must record one row for
every configured reviewer and labeled gate decision, and must prove the report
is observation-only: no gate aggregation, reviewer roster default, calibrated
weight, or policy setting may change. Ledger writes at this boundary are eval
observations only, not gate decisions.

## reviewer_panel_adjudication

Adjudicate reviewer-panel disagreement through `run_dual_agent_workflow` and
the emitted `independent_reviewer_review` /
`independent_reviewer_adjudication` events. Tests must exercise the workflow
boundary first, with live reviewers injected below that boundary, and prove
that split panels or important/critical accept-shaped objections produce a
bounded evidence packet without majority vote, calibrated weighting, unbounded
file reads, or any weakening of real revise/deny hard blocks.

## mergeability_fixture_measurement

Run fixture-scale mergeability calibration through
`run_fixture_panel_produced_baseline_measurement`. Tests must exercise the
public runner before helper-only receipt builders, inject configured reviewer
adapters below the runner seam for deterministic unit coverage, and prove the
exported paired acceptance report records the primary
`supervisor_vs_single_agent_baseline` comparison, reviewer packet hashes,
reviewer rationales, unavailable-panel failure, and report-only authority flags.
Rows already rejected by the public floor may short-circuit as available
full-gate rejects with `public_review_rejected`; reviewer verdicts remain
mandatory for every row that the public floor would otherwise accept.

## reviewer_panel_aggregation

Aggregate independent reviewer verdicts through `evaluate_reviewer_panel` and
`ConfiguredReviewerPanelOptions.panel_aggregation_mode`. Tests must prove
hard-block precedence holds in every mode: critical/important revise or deny
still produces `revise`, missing verdicts still produce `revise`, and
low-confidence accept still escalates. `"conservative"` (default for direct
callers) must preserve all-reviewers-accept ordering; `"geometric_median"`
(default for SWE-bench replay CLIs) is allowed only to settle the remaining
accept-vs-revise tie. When robust mode is requested the panel decision must
record `aggregation_mode="geometric_median"` with a `robust_aggregation`
summary, and any active calibration must be dropped so robust precedence is
visible rather than silently overwritten.

## reviewer_provider_family_verification

Resolve a reviewer's provider family through
`provider_family_verification_for_reviewer` and the
`_result_with_spec_provenance` provenance helper. Tests must prove that a
served-model match (`served_model`/`response_model`/`response_metadata`) sets
`provider_family_verified=true`, that operator-named families are recorded with
`provider_family_source="operator_config"` and not counted as verified, and
that downstream provenance reports preserve the verified flag. The official
all-arms diagnostic verifier must additionally treat
`unverified_provider_family`,
`operator_asserted_provider_family_unverified`, and any official-replay row
without `panel_aggregation_mode == "geometric_median"`
(`reviewer_panel_not_robustly_aggregated`) as cross-family readiness blockers
without flipping any authority flag.

## swe_bench_mergeability_replay_cli

Drive deterministic SWE-bench mergeability replay, live, and official-all-arms
diagnostic runs through `codex-supervisor-swebench-mergeability-replay`
(`supervisor.swe_bench_mergeability_cli:main`). Tests must prove the CLI
forwards `--reviewer-panel-mode`, `--reviewer-output-mode`,
`--reviewer-model`, `--codex-model`, `--litellm-model`,
`--litellm-provider-family`, `--panel-aggregation-mode`, `--oracle-adapter`,
`--oracle-adapter-kind`, `--swe-bench-pro-scripts-dir`, and the dataset-fetch
and budget guards to the underlying runners, refuses official replay without
`--allow-dataset-fetch`, `--dataset`, `--predictions`, `--oracle-adapter`, and
a supported adapter kind, refuses live runs without `--allow-live`,
`--max-budget-usd > 0`, and both generator commands, and writes a blocked
all-arms diagnostic artifact when prerequisites are missing. The CLI must not
execute reviewer or oracle work itself; it composes the public runners under
the supervisor seams above.
