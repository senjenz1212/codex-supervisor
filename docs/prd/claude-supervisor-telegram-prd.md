# Claude Supervisor Over Telegram PRD

## Status

Draft v0.15 — Mode toggles are approval-gated Telegram commands.

## Changelog

- v0.9 — Added CS11: outbound watched-run progress notifications are persisted
  as supervisor-visible conversation context, so follow-up prompts like
  "what's your suggestion?" see the latest run-complete/merged/blocked state
  instead of answering from stale prior turns.
- v0.12 — Added CS15: Desktop GUI reflection work must first run a bounded
  cold-start vs normal-turn IPC capture/diff spike and produce an evidence
  branch before any production GUI-live claim or transport change.
- v0.13 — Added CS21: aggressive steering mode may auto-deliver
  non-destructive steering through the action ledger, while destructive
  escalation remains approval-gated.
- v0.14 — Added CS22: `telegram_fyis: off` suppresses routine progress and
  review pings while preserving quiet context, auto-steer, alerts, and approval
  prompts.
- v0.15 — Added CS23: `/autosteer` and `/quiet` Telegram commands can request
  mode changes, but only nonce-approved callbacks mutate config and restart the
  daemon.
- v0.11 — Added CS14: Desktop status sync reports an effective visibility
  state. App-server `thread/inject_items` success is treated as history-only
  unless the adapter explicitly proves live GUI repaint.
- v0.10 — Added CS13: watched-run progress can be mirrored into Codex Desktop
  history as passive supervisor status items, with GUI repaint explicitly
  reported as verified or unverified.
- v0.8 — Added CS10: the supervisor can append passive status items into a
  Codex Desktop thread through Codex app-server `thread/inject_items`, so the
  target chat history can reflect supervisor state without starting a new agent
  turn.
- v0.4 — CS7 implemented with per-chat conversation rows, SDK session id
  resume, MCP traceback, and thresholded rolling summary refresh.
- v0.5 — Added CS8: Telegram can watch a Codex Desktop run and receive
  throttled high-signal progress updates from rollout ingestion without
  waiting for a conversational query.
- v0.6 — Added CS9: after watched-run updates, Claude can review the actual
  target workspace snapshot, changed files, and safe file contents before
  sending recommendations.
- v0.7 — Closed CS4 steering gap: natural-language steer requests now create
  nonce-protected Telegram approval actions before Codex receives any steer.
  Telegram mode changes were deferred at this stage and are not exposed as
  Claude tools.
- v0.3 — Added CS7: Telegram conversations must feel continuous across turns,
  daemon restarts, and SDK compaction by combining Claude SDK session resume
  with supervisor-owned SQLite memory and rolling summaries.
- v0.2 — Clarified that "Vela chat bot" is a Codex Desktop session name, not
  Slack. Added implementation slices for named-session resolution, supervisor
  tool API, Telegram chat ingress, turn replay, and mode-safe actions.
- v0.1 — Initial pivot from daemon-as-brain to Claude-as-supervisor.

## Problem Statement

Sam wants to talk to the supervisor naturally from Telegram while Codex Desktop
works. The current system can observe Codex Desktop, audit hooks, send Telegram
notifications, and call Claude Agent SDK for bounded judgments, but the Python
daemon is still the conversation router. That means every new Telegram behavior
requires another bespoke command.

The desired experience is different: Claude should be the supervisor. Telegram
messages should become supervisor turns. Claude should inspect local run state,
Codex rollouts, hook history, and actions through tools, then respond naturally
and propose safe actions.

## Solution

Reframe the architecture:

- The daemon remains the local **sensor and tool plane**: hook server, rollout
  watcher, SQLite state, redaction, deterministic fallback rules, Telegram
  polling, and durable action ledger.
- Claude Agent SDK becomes the **conversational supervisor runtime** for
  Telegram turns. It receives the user's Telegram message plus a compact run
  context and can call supervisor tools.
- Codex Desktop remains the **supervised target**. It is observed through
  rollouts and hooks now, with app-server reserved as a future richer target
  surface.
- Human-facing session names such as "Vela chat bot" resolve to Codex sessions
  through local session metadata, registry files, rollout content, and explicit
  aliases. "Vela chat bot" is a Codex Desktop session name, not a Slack bot.

## User Stories

1. As Sam, I can send a normal Telegram message to the supervisor and get a
   useful answer instead of memorizing slash commands.
2. As Sam, I can ask "what is happening in Vela chat bot?" and the supervisor
   resolves that named Codex Desktop session to the correct local rollout.
3. As Sam, I can ask "is it drifting?" and Claude inspects recent events,
   hooks, scope findings, and actions before answering.
4. As Sam, I can ask the supervisor to watch a named session more closely, and
   it records that watch preference without changing global modes.
5. As Sam, I can ask why a hook was allowed or warned, and the supervisor cites
   stored hook/event/action ids.
6. As Sam, I can request a nudge or re-anchor, but steering still goes through
   the action ledger and approval/mode policy.
7. As Sam, I can change the two supported Telegram-facing modes from Telegram
   only through explicit, confirmation-protected commands.
8. As Sam, I can replay any supervisor Telegram turn from stored inputs and
   model/tool outputs.
9. As Sam, I can keep deterministic fallback rules for hard safety even when
   Claude is slow, unavailable, or wrong.
10. As Sam, I can later swap Codex rollout watching for Codex app-server without
    changing the Telegram conversation contract.
11. As Sam, I can keep talking to the supervisor from Telegram and have it
    remember the current thread, recent questions, current monitored session,
    and pending approvals without me restating everything.
12. As Sam, I can ask the supervisor to watch a Codex Desktop session and get
    concise real-time progress updates in Telegram when important rollout
    events happen, without receiving token-count/tool-noise spam.
13. As Sam, I can receive Claude suggestions that are grounded in the actual
    worktree Codex is editing, including git status/diff and bounded file
    reads, not only rollout summaries.
14. As Sam, I can have supervisor status corrections appear in the Codex
    Desktop thread history without me typing a prompt into Desktop and without
    starting another Codex agent turn.
15. As Sam, when the supervisor pushes a run-complete or progress notification
    to Telegram, my next follow-up question sees that notification as part of
    the same conversation.
16. As Sam, if Desktop status sync writes history but my open Desktop chat does
    not visibly update, the supervisor tells the truth: history append
    succeeded, GUI-live repaint is unverified, and Telegram remains the
    reliable live stream.
17. As Sam, before we spend days on Desktop GUI reflection, I get a bounded
    evidence-gated answer: existing method, forced reload, or external Codex
    handler needed.
18. As Sam, I can ask the supervisor to continue with suggested low-risk moves
    automatically, while it only pings me for escalation, destructive recovery,
    blocked delivery, or ambiguous authority.
19. As Sam, I can put Telegram in quiet mode so watched-run progress and
    review FYIs do not ping me, while the supervisor still remembers progress
    and still pings me for alerts, approval, or escalation.
20. As Sam, I can turn autosteer and quiet mode on/off from Telegram, but the
    supervisor must ask me for a button approval before changing live config.

## PRD Promise Contracts

### CS1. Telegram Is a Conversational Supervisor Surface

User-visible promise: A Telegram text message that is not a known slash command
starts a Claude supervisor turn and returns a natural-language answer.

Representative actions: Send "what are you watching?", "what is happening in
Vela chat bot?", or "why did you allow the last hook?" to the Telegram bot.

Public boundary: `telegram_chat_ingress`.

Allowed outcomes: message is persisted, redacted, sent to Claude supervisor
runtime with local tools, response is sent back to Telegram, failure is surfaced
as a short degraded-state message.

Forbidden outcomes: free-form Telegram text is ignored; Telegram message causes
an external action without an action ledger row; raw secrets are sent to Claude
or Telegram.

### CS2. Named Codex Sessions Resolve to Concrete Run State

User-visible promise: The supervisor can resolve a human session name such as
"Vela chat bot" to a Codex session id, rollout path, and current run state.

Representative actions: Ask "monitor Vela chat bot" or "summarize Vela chat
bot".

Public boundary: `named_session_resolver`.

Allowed outcomes: exact alias resolves; recent rollout task/title match
resolves; ambiguous matches ask the user to choose; no match returns a helpful
not-found response with recent session candidates.

Forbidden outcomes: treats "Vela chat bot" as a Slack bot by default; guesses
between ambiguous sessions; uses stale completed sessions when a newer active
session exists.

### CS3. Claude Supervisor Uses Tools, Not Raw Database Coupling

User-visible promise: Claude answers by calling stable supervisor tools rather
than reading raw SQLite or target-specific payloads.

Representative actions: Claude calls `list_runs`, `resolve_session`,
`read_recent_events`, `read_hooks`, `read_actions`, `evaluate_scope`,
`propose_action`, `request_steering`.

Public boundary: `supervisor_tool_api`.

Allowed outcomes: tools return redacted, normalized, bounded JSON with event
ids; mutating tools require mode checks and write action rows.

Forbidden outcomes: Claude receives raw unredacted hook payloads; Claude uses
SQL directly; tools expose target-specific Codex JSON shapes to supervisor
prompts.

### CS4. Telegram Requests Cannot Bypass Approval and Modes

User-visible promise: A natural-language request like "stop it" or "nudge it"
becomes a proposed action and follows mode/approval policy. Free-text requests
to change operating modes must not mutate live configuration.

Representative actions: Send "re-anchor Vela chat bot"; ask for a mode change
in free text and receive an unsupported/deferred response rather than a silent
config change.

Public boundary: `telegram_chat_ingress` plus `action_executor`.

Allowed outcomes: safe read-only answers are immediate; in `advise`, steering
requests create pending action rows and nonce-protected Telegram approval
prompts before Codex receives a message; in `enforce`, non-destructive
steering may auto-deliver through the action ledger; mode-change requests are
not executed in v0.7; destructive actions require fresh Telegram confirmation.

Forbidden outcomes: Telegram free text directly kills, restarts, blocks, or
injects steering without an action ledger row and mode check; Telegram free
text changes modes or live config; stale approval buttons execute actions;
shadow or advise mode mutates target state.

### CS5. Supervisor Turns Are Replayable

User-visible promise: A Telegram conversation turn can be replayed from frozen
message, context snapshot, tool outputs, and model output fixtures.

Representative actions: Replay "what is happening in Vela chat bot?" and get
the same answer/action classification.

Public boundary: `supervisor_turn_replay`.

Allowed outcomes: replay does not call live Telegram, Slack, Codex, or model
APIs; output includes response text, cited ids, tool calls, and proposed
actions.

Forbidden outcomes: replay depends on current config, live sessions, live
Telegram, live model calls, or non-redacted payloads.

### CS6. Codex App-Server Is a Future Target Surface, Not Required Now

User-visible promise: The current implementation can monitor Codex Desktop via
rollouts/hooks now, and a future app-server adapter can replace the target
event source without changing Telegram conversation behavior.

Representative actions: Run the same Telegram supervisor turn against rollout
fixtures and, later, app-server event fixtures.

Public boundary: `target_adapter_conformance`.

Allowed outcomes: app-server support is represented as an adapter capability;
rollout/hook monitoring remains the default; unsupported app-server features
return degraded states.

Forbidden outcomes: Telegram conversation code imports app-server-specific
payloads; Codex Desktop monitoring is blocked until app-server is implemented.

### CS7. Telegram Conversations Feel Continuous

User-visible promise: A Telegram conversation with the supervisor feels like one
ongoing thread. The supervisor remembers recent turns, the active monitored
session, previous answers, and pending next steps even though each daemon
response is bounded and restart-safe.

Representative actions: Send "monitor Vela chat bot", then later send "what
changed?", "can you use the same PR?", or "why did you say it was stalled?"
without repeating the session name.

Public boundary: `telegram_chat_ingress` plus `supervisor_tool_api`.

Allowed outcomes: each turn is persisted; Claude receives a redacted continuity
pack with recent Telegram turns and conversation metadata; the Claude SDK
session id is stored when available and resumed when safe; a rolling summary can
compact old turns; raw turns remain available for traceback and replay.

Forbidden outcomes: the supervisor relies only on an in-memory Claude process;
daemon restart erases conversation continuity; Claude sees unredacted Telegram
history; current user messages are duplicated as prior memory; stale memory
causes an external action without a fresh action ledger row.

### CS8. Watched Runs Stream High-Signal Progress to Telegram

User-visible promise: After Sam watches a run or named Codex Desktop session,
the supervisor pushes concise Telegram progress updates for important rollout
events without Sam needing to ask again.

Representative actions: Ask "watch Vela chat bot", then let Codex Desktop
continue working; observe Telegram updates for task start, assistant progress,
task completion, PR/check/test status, and halted/approval states.

Public boundary: `event_ingestion_api` plus `supervisor_tool_api`.

Allowed outcomes: watches are durable per chat/run; rollout ingestion invokes a
progress router; token-count/reasoning/noise events are ignored; duplicate
event ids are not resent; Telegram messages are redacted and concise; failures
to send do not block rollout ingestion.

Forbidden outcomes: every rollout line is sent to Telegram; secret material or
raw rollout payloads are sent to Telegram; stale historical backfill floods the
chat; watching a run performs steering or other target mutation; daemon restart
loses watch state.

### CS9. Claude Suggestions Are Grounded in the Target Workspace

User-visible promise: When the supervisor offers recommendations after a run
update, Claude reviews the concrete workspace Codex is operating in before
advising.

Representative actions: A watched run finishes a turn; the supervisor invokes a
bounded review; Claude reads recent rollout events, the resolved worktree git
status/diff, and specific changed files before sending suggestions.

Public boundary: `supervisor_tool_api` plus `agent_invoker`.

Allowed outcomes: workspace access is derived from run events or run metadata;
tools expose redacted git status/diff and safe file reads under the workspace
root; Claude cites concrete files/events in recommendations; review failures do
not block rollout ingestion or progress notifications.

Forbidden outcomes: Claude recommendations are based only on conversation
memory; file reads escape the workspace root; secret files or raw tokens are
exposed; reviews mutate the target workspace; a review is triggered for every
low-signal rollout event.

### CS10. Supervisor Status Can Be Appended To Codex Desktop History

User-visible promise: When the supervisor discovers a durable state correction
or high-signal status that should also be visible in the supervised Codex
Desktop thread, it can append a passive assistant-style status item into that
thread history without requiring Sam to prompt Desktop manually.

Representative actions: The supervisor records that 18c.5 grill approval is
already durable in the target worktree, then sends an `append_status_item`
target action for the "Vela chat bot" Codex session.

Public boundary: `codex_app_server_status_sync`.

Allowed outcomes: the Codex adapter uses Codex app-server JSON-RPC
`thread/inject_items`, preferably through `codex app-server proxy` to the
running Desktop/managed app-server when remote control is available; the
injected item is clearly marked as supervisor status; secrets are redacted
before the item is written; failures return `delivered=false` with a reason; if
Desktop does not repaint live, the result must say that GUI repaint is
unverified rather than pretending the GUI changed.

Forbidden outcomes: the passive status path uses `codex exec resume` or starts
a Codex user/agent turn; the implementation requires GUI focus, paste
automation, or a visible browser; raw Telegram tokens, API keys, or control
secrets are written into the thread; the supervisor silently creates a third
unverified state that disagrees with Telegram and rollout history.

### CS11. Follow-Up Suggestions See Latest Watched-Run Progress

User-visible promise: After the supervisor sends Sam a watched-run progress
notification, the next Telegram supervisor turn receives that notification in
its continuity context. A prompt like "what's your suggestion?" should not
answer as if the run is still mid-turn when the immediately previous
supervisor-visible notification said the run completed or shipped.

Representative actions: The watched Vela session emits a `task_complete`
notification saying PR #57 merged and 18c.5 shipped. Sam then sends "What's ure
suggestion?" in Telegram.

Public boundary: `telegram_progress_context`.

Allowed outcomes: successful progress notifications are persisted as
supervisor-visible, redacted conversation context; the conversation's
`active_run_id` is updated to the watched run; the current user message is not
duplicated as prior memory; runtime prompt context includes the progress
notification before the model answers.

Forbidden outcomes: outbound progress notifications are invisible to the next
supervisor turn; Claude receives raw unredacted rollout payloads; a stale
conversation summary overrides a newer progress notification; a progress
notification grants permission to steer, approve, kill, restart, or merge.

### CS12. Already-Sent Progress Can Be Safely Repaired Into Context

User-visible promise: If a watched-run progress notification was sent before
the supervisor learned to persist outbound notifications, Sam can repair that
conversation memory without resending Telegram, creating duplicate turns, or
accidentally writing the row under a blank chat id.

Representative actions: The Vela watched run already emitted event `37207`
saying PR #57 merged and 18c.5 shipped. An operator runs the backfill command
for that `run_id` and `event_id`, then the next Telegram follow-up sees the
same run-complete notification as recent context.

Public boundary: `progress_context_backfill`.

Allowed outcomes: the repair loads the configured state DB, requires a real
chat id, reads the stored event, formats the same redacted progress message,
records one completed supervisor notification turn, updates the conversation's
`active_run_id`, and reports whether it recorded or skipped as already present.

Forbidden outcomes: the repair sends a second Telegram notification; writes a
blank-chat supervisor turn; records duplicate notification rows for the same
chat/run/event; stores unredacted rollout secrets; creates any steering,
approval, kill, restart, or merge authority.

### CS13. Watched-Run Progress Can Mirror Into Codex Desktop History

User-visible promise: When Sam watches a Codex Desktop session from Telegram,
the same high-signal progress events can also be appended into the Codex thread
history as passive supervisor status messages. Telegram remains the reliable
live stream; Desktop status sync is an additional history/visibility surface.

Representative actions: The Vela session emits `Run started`, commentary, or
`Run complete`. The supervisor sends the Telegram progress message and, when
`modes.desktop_status_sync` is enabled, submits `append_status_item` for the
same run/session/event.

Public boundary: `desktop_progress_status_sync`.

Allowed outcomes: Desktop sync only runs for watched, high-signal progress
events; it records an `append_status_item` action row; it uses the Codex
adapter app-server path, not `codex exec resume`; failures are audited and do
not block Telegram delivery; results preserve `desktop_gui_repaint` as
`unverified` unless a live GUI smoke test proves repaint.

Forbidden outcomes: Desktop sync starts a new Codex turn; failure to append to
Desktop suppresses Telegram progress; the supervisor claims GUI repaint without
proof; status sync creates steering, approval, kill, restart, or merge
authority; duplicate old rollout events are replayed into Desktop.

### CS14. Desktop GUI Repaint Truth Is Not Implied By History Append

User-visible promise: When Codex app-server accepts a passive
`thread/inject_items` write, the supervisor classifies the result as either
`gui_live`, `history_only`, `failed`, or `off`. A delivered write with
`desktop_gui_repaint=unverified` must be reported as `history_only`, not as a
Desktop GUI reflection.

Representative actions: A watched Vela run emits `Run complete`; Telegram
receives the update; Desktop status sync appends a passive item through
app-server; Sam reports that the open Codex Desktop chat did not visibly
change.

Public boundary: `desktop_status_visibility_policy` plus
`desktop_progress_status_sync`.

Allowed outcomes: action rows include a `visibility` object with
`effective_state`, `history_appended`, `gui_repaint`, and `user_visible_summary`;
`/health` exposes the effective Desktop status sync behavior as history-only
when the live GUI path is unverified; failures still do not block Telegram
progress; a future adapter may report `gui_repaint=verified` and then the
effective state may become `gui_live`.

Forbidden outcomes: any Telegram response, health response, action payload, or
operator-facing status says Desktop GUI reflected, mirrored live, or became
visible when the only proof is `thread/inject_items`; `delivered=true` is used
as a synonym for GUI-visible; the supervisor keeps silently writing a second
state surface that Sam expects to see live.

### CS15. Desktop GUI Reflection Viability Is Evidence-Gated

User-visible promise: Before the supervisor claims Codex Desktop GUI reflection
is near-term or enables a GUI-live transport, it runs a bounded IPC spike that
compares Desktop cold-start hydration with a normal GUI turn and records a
sanitized evidence branch.

Representative actions: Capture Desktop IPC while the app opens an existing
thread from cold start; capture IPC while Sam manually sends a harmless normal
Codex Desktop turn; run the CS20 capture diff and decide between an existing
method, a forced-reload method, or a Codex external-handler request.

Public boundary: `codex_desktop_ipc_capture_diff`.

Allowed outcomes: the spike stores method names, message types, stream change
types, and patch paths only; it recommends the next probe branch; it preserves
`desktop_gui_repaint=unverified` until a human-visible or renderer-observed
smoke proves otherwise; it keeps live config unchanged.

Forbidden outcomes: the spike stores prompt text, patch values, secrets, or raw
IPC payload values; fixture tests touch the real Desktop IPC socket; live
experiments start or steer a Codex turn without explicit HITL; the supervisor
treats history append, cache invalidation, or stream observation as GUI-visible
reflection proof.

### CS21. Aggressive Steering Proceeds Only Within Escalation Policy

User-visible promise: When `modes.steering_injection` is `enforce`, the
supervisor can automatically deliver non-destructive steering such as
"continue", "re-anchor", or "do the suggested next low-risk step" through the
action ledger. It should ping Sam only when the request is destructive,
ambiguous, blocked, failed, or outside the allowlist.

Representative actions: Send "continue with your suggestion" or "re-anchor the
Vela chat bot on the approved issue" to Telegram while steering is in
`enforce`; ask for kill/restart/destructive recovery; repeat the same steer
twice.

Public boundary: `telegram_chat_ingress` plus `supervisor_tool_api` plus
`action_executor`.

Allowed outcomes: `advise` mode still creates a nonce-protected Telegram
approval prompt; `enforce` mode records a durable `inject_steering` action row
and may deliver it through the target adapter without an approval prompt;
duplicates are deduped; failures are recorded; kill/restart/destructive
recovery still require fresh approval or remain unavailable.

Forbidden outcomes: auto-steering runs without an action row; `advise` mode
auto-delivers; `enforce` mode auto-executes kill, restart, or destructive
recovery; stale Telegram approval callbacks mutate the target; Claude changes
live modes from free text; a failed auto-steer is reported as delivered.

### CS22. Quiet Mode Suppresses FYIs But Preserves Escalation

User-visible promise: When `modes.telegram_fyis` is `off`, the supervisor does
not send routine watched-run progress or review/advice Telegram pings. It still
records the progress as quiet context, advances watch offsets, keeps automatic
non-destructive steering available through CS21, and lets alerts plus approval
prompts reach Sam.

Representative actions: Set `telegram_fyis: off`, watch "Vela chat bot", let a
run emit `Run started` or `Run complete`, and let a `review_updates` decision
try to send a normal or FYI Telegram message. Trigger an alert-path message or
an approval prompt.

Public boundary: `telegram_progress_context` plus `telegram_mcp_tools`.

Allowed outcomes: progress events are not sent to Telegram in quiet mode; the
event is persisted as suppressed supervisor context with `active_run_id`; the
watch offset advances so the same event is not replayed; quiet mode still
enqueues grounded review work; Telegram MCP `send_message` suppresses
`normal`/`fyi` messages but allows `alert`; approval prompts and destructive
escalation gates remain available.

Forbidden outcomes: quiet mode forgets progress, replays the same event later,
suppresses alerts or approval prompts, disables auto-steer, sends routine
review/progress pings anyway, or lets a suppressed FYI pretend it was sent.

### CS23. Telegram Mode Toggles Require Approval

User-visible promise: Telegram commands `/autosteer on|off` and `/quiet on|off`
can request live mode changes, but they must create a nonce-protected approval
prompt before mutating `~/.codex-supervisor/config.yaml` or restarting the
daemon.

Representative actions: Send `/autosteer on`, tap Approve, then verify
`modes.steering_injection=enforce`; send `/quiet on`, tap Reject, and verify
`modes.telegram_fyis` is unchanged.

Public boundary: `telegram_chat_ingress` callback handling.

Allowed outcomes: commands from the configured chat create a `mode_change`
action and Telegram ask; config stays unchanged until approval; Approve updates
only `steering_injection` or `telegram_fyis`, restarts the launch agent, and
marks the action `applied`; if restart reports failure, the config file is
rolled back and the action is marked `failed`; Reject marks the action
`cancelled`; stale or spoofed callbacks fail closed.

Forbidden outcomes: free text changes modes; a slash command changes config
without approval; non-allowlisted mode keys are writable; rejected/stale
callbacks mutate config; restart failure is reported as success or leaves a
partially-applied config file behind.

## Non-Goals

- Replacing Codex Desktop.
- Building a polished web UI.
- Letting Telegram free text perform destructive actions directly.
- Letting Telegram free text change supervisor modes.
- Treating "Vela chat bot" as Slack unless the user explicitly configures a
  Slack source.
- Guaranteeing that an already-open Desktop renderer repaints immediately after
  an external app-server write. The v2 path guarantees the thread-history write
  and reports repaint verification separately.
- Continuing Desktop GUI reflection exploration indefinitely after the bounded
  CS20 spike fails to find an external method or reload path.

## Operating Modes

The Claude supervisor may answer read-only questions in all modes. Any mutation
must go through the existing action/mode policy:

- `shadow`: answer only, record would-do.
- `advise`: recommend and ask.
- `enforce`: execute only allowed non-destructive actions; destructive actions
  still ask.

## Open Questions

1. Where does Codex Desktop store the human session name reliably? If rollout
   metadata does not include it, v1 needs an alias registry.
2. Should Telegram chat create per-session watches, global watches, or both?
3. Should mode changes require a second confirmation even when non-destructive?
4. Should the rolling-summary threshold become config-driven instead of the
   current fixed 10-turn cadence?
