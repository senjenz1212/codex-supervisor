# 09 - Grounded Review Suggestions

## PRD Promise

Promise ID: CS9 - Claude Suggestions Are Grounded in the Target Workspace.

User-visible promise: when the supervisor offers recommendations after a run
update, Claude reviews the concrete workspace Codex is operating in before
advising.

Public boundary for first RED test: `supervisor_tool_api`.

Representative action: a watched run finishes a turn; Claude reviews recent
rollout events plus the resolved worktree git status/diff and sends concise
recommendations to Telegram.

Allowed outcomes:

- Workspace access is derived from run events or run metadata.
- Grounding tools expose redacted git status/diff and bounded safe file reads.
- File reads are constrained under the resolved workspace root.
- The review is notification-only and never mutates the target.
- Review failures do not block rollout ingestion or progress streaming.
- Automatic review is triggered only after a successfully delivered watched
  `task_complete` progress notification.

Forbidden outcomes:

- Claude recommendations are based only on conversation memory.
- File reads escape the workspace root.
- Secret files or raw tokens are exposed.
- Reviews mutate the target workspace.
- A review is triggered for every low-signal rollout event.
- The reviewer skill can call steering, kill, restart, or arbitrary shell
  tools.

## TDD Plan

Cycle 1 - RED/GREEN: workspace grounding tools.

- RED: construct a run whose recent events include a concrete workdir, create
  a git repo with changed files, and call `SupervisorToolAPI.read_workspace_snapshot`.
  Assert the returned snapshot includes the worktree, changed files, diff, and
  redaction.
- GREEN: implement bounded workspace resolution and snapshot generation.

Cycle 2 - RED/GREEN: safe file access.

- RED: call `read_workspace_file` for an in-workspace file and for `../escape`.
  Assert the first returns redacted content and the second fails closed.
- GREEN: add safe path containment, binary/size limits, and secret path denies.

Cycle 3 - RED/GREEN: automatic review trigger.

- RED: watched `task_complete` progress enqueues exactly one `review_updates`
  decision after the Telegram progress notification is sent; token-count/noise
  events do not.
- GREEN: wire `TelegramProgressStreamer` to enqueue review decisions after
  successful task-complete progress delivery.

Cycle 4 - GREEN: Claude reviewer skill.

- Add `skills/review-updates.md`.
- Add `review_updates` to `AgentInvoker`.
- Allow MCP tools for rollout, metadata, workspace snapshot, workspace file
  reads, and Telegram send. Do not allow steering or target mutation tools.

## Grill Findings Incorporated

- GR1/GRT2: "grounded" means bounded workspace snapshot plus safe file reads,
  never raw filesystem access.
- GR2: vocabulary remains target-neutral even though Codex rollouts provide the
  current workdir source.
- GR3/GRT4: review suggestions are advice-only and use read-only tools.
- GR4/GRT3: review triggering sits behind the progress streamer's high-signal
  and successful-notification filter.

## Implementation Hardening Notes

- Added a regression that `.env` variants such as `.env.production` are denied
  by the workspace file tool and omitted from changed-file snapshots.
- Added a regression that the Codex MCP `get_run_metadata` tool can read
  completed runs, so delayed `review_updates` decisions still have run
  metadata if the rollout terminal event lands before the reviewer executes.
