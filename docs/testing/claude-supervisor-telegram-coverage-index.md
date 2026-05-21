# Claude Supervisor Telegram PRD Coverage Index

Source PRD: `docs/prd/claude-supervisor-telegram-prd.md`

| Promise | Coverage | Issue |
|---|---|---|
| CS1 Telegram Is a Conversational Supervisor Surface | Covered | `.scratch/claude-supervisor-telegram/03-telegram-chat-ingress.md` |
| CS2 Named Codex Sessions Resolve to Concrete Run State | Covered | `.scratch/claude-supervisor-telegram/02-named-session-resolver.md` |
| CS3 Claude Supervisor Uses Tools, Not Raw Database Coupling | Covered | `.scratch/claude-supervisor-telegram/01-supervisor-tool-api.md` |
| CS4 Telegram Requests Cannot Bypass Approval and Modes | Covered for steering and free-text mode safety | `.scratch/claude-supervisor-telegram/05-mode-safe-telegram-actions.md` |
| CS5 Supervisor Turns Are Replayable | Covered | `.scratch/claude-supervisor-telegram/04-supervisor-turn-replay.md` |
| CS6 Codex App-Server Is a Future Target Surface, Not Required Now | Deferred | `.scratch/claude-supervisor-telegram/06-codex-app-server-adapter-spike.md` |
| CS7 Telegram Conversations Feel Continuous | Covered | `.scratch/claude-supervisor-telegram/07-conversation-continuity.md` |
| CS8 Watched Runs Stream High-Signal Progress to Telegram | Covered | `.scratch/claude-supervisor-telegram/08-telegram-progress-streaming.md` |
| CS9 Claude Suggestions Are Grounded in the Target Workspace | Covered | `.scratch/claude-supervisor-telegram/09-grounded-review-suggestions.md` |
| CS10 Supervisor Status Can Be Appended To Codex Desktop History | In progress | `.scratch/claude-supervisor-telegram/10-codex-desktop-status-sync.md` |
| CS11 Follow-Up Suggestions See Latest Watched-Run Progress | Covered | `.scratch/claude-supervisor-telegram/11-progress-context-continuity.md` |
| CS12 Already-Sent Progress Can Be Safely Repaired Into Context | Covered | `.scratch/claude-supervisor-telegram/12-progress-context-backfill.md` |
| CS13 Watched-Run Progress Can Mirror Into Codex Desktop History | Covered | `.scratch/claude-supervisor-telegram/13-desktop-progress-status-sync.md` |
| CS14 Desktop GUI Repaint Truth Is Not Implied By History Append | In progress | `.scratch/claude-supervisor-telegram/14-desktop-gui-repaint-truth.md` |
| CS15 Desktop GUI Reflection Viability Is Evidence-Gated | In progress | `.scratch/claude-supervisor-telegram/20-cold-start-normal-turn-ipc-spike.md` |
| CS21 Aggressive Steering Proceeds Only Within Escalation Policy | Covered | `.scratch/claude-supervisor-telegram/21-aggressive-steering-escalation-policy.md` |
| CS22 Quiet Mode Suppresses FYIs But Preserves Escalation | Covered | `.scratch/claude-supervisor-telegram/22-quiet-mode-escalation-only.md` |
| CS23 Telegram Mode Toggles Require Approval | Covered | `.scratch/claude-supervisor-telegram/23-telegram-mode-toggle-approval.md` |

## Implemented Tests

- `tests/test_named_session_resolver.py`
- `tests/test_supervisor_tool_api.py`
- `tests/test_telegram_chat_ingress.py`
- `tests/test_supervisor_turn_replay.py`
- `tests/test_action_executor.py`
- `tests/test_codex_resume.py`
- `tests/test_telegram_progress_streaming.py`
- `tests/test_telegram_live.py`
- `tests/test_telegram_quiet_mode.py`
- `tests/test_workspace_grounding.py`
- `tests/test_agent_invoker_review.py`
- `tests/test_codex_mcp_tools.py`
- `tests/test_codex_app_server_status_sync.py`
- `tests/test_telegram_progress_context.py`
- `tests/test_progress_context_backfill.py`
- `tests/test_desktop_status_progress_sync.py`
- `tests/test_codex_desktop_ipc_capture_diff.py`

## Deferred

- Full Codex app-server event/approval replacement remains deferred. The v2
  path only appends passive status items through app-server while rollout and
  hook monitoring remain the production observation path.
- Full Python-side natural-language action extraction is deferred. Claude can
  request steering through `mcp__supervisor__request_steering`; execution stays
  behind `action_executor`. In `advise`, Telegram approval is required. In
  `enforce`, non-destructive steering may auto-deliver; destructive actions
  still require approval.
- Generic Telegram mode changes remain deferred. No mode-change MCP tool is
  exposed, and live config cannot be changed from free text. CS23 adds only the
  two approval-gated slash commands `/autosteer` and `/quiet`; their command
  matrix, configured-chat authority, stale callback behavior, allowlist, and
  restart-failure handling are covered in `tests/test_telegram_live.py`.
- Quiet mode suppresses routine watched-run progress, but blocker progress
  such as HALTED/sandbox-blocked runs is covered as an alert bypass in
  `tests/test_telegram_progress_streaming.py`.
- Rolling model-generated summary refresh is implemented at the Telegram
  ingress boundary. Every thresholded conversation turn refreshes
  `supervisor_conversations.summary` with a fail-soft summarizer while raw
  `supervisor_turns` remain available for audit.
- Watched-run progress streaming is implemented through durable `run_watches`
  rows plus rollout-ingestion callbacks. The first proof starts at
  `RolloutWatcher._drain_file`, not a formatter helper.
- Grounded review suggestions are implemented through workspace snapshot/file
  tools and a `review_updates` decision. The reviewer can inspect actual git
  status/diff and bounded safe files before sending Telegram advice.
