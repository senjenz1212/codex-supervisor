# Forbidden Outcomes

These outcomes are forbidden across the Agent Supervisor PRD unless a narrower
issue explicitly adds a stricter rule.

- Shadow mode performs external action.
- Advise mode blocks, kills, restarts, or injects steering.
- Enforce mode injects steering without a durable action row, or auto-executes
  kill/restart/destructive recovery without fresh user approval.
- Destructive action runs without fresh user approval.
- Stale or spoofed Telegram callback performs an action.
- Telegram free text or slash commands mutate live modes without a fresh
  nonce-protected approval callback.
- Telegram mode-toggle approval reports success after launch-agent restart
  failure or leaves the config file changed when restart fails.
- A secret appears in SQLite or Telegram text.
- Replay calls live target agents, live Telegram, or live model APIs by default.
- Drift, replay, action policy, or decision runtime reads raw Claude Code or
  Codex payloads directly instead of normalized events.
- Codex-specific commands run when `target.kind` is `claude_code`.
- Passive Codex Desktop status sync starts a Codex user turn or calls
  `codex exec resume`.
- Passive Codex Desktop status sync claims GUI repaint without verification.
- Desktop progress status sync failure suppresses Telegram progress.
- Desktop progress status sync treats `delivered=true` as equivalent to
  visible Desktop GUI repaint.
- Desktop GUI steering runs without the existing approval path or is used for
  passive watched-run progress.
- Desktop GUI steering failure silently falls back to invisible CLI resume
  delivery.
- Desktop IPC refresh probes start or steer Codex turns.
- Desktop IPC cache-invalidation broadcasts are reported as visible GUI repaint
  without a human-visible or renderer-observed verification signal.
- Desktop IPC stream decoding sends anything beyond the initial observer
  `initialize` request or treats decoded snapshot/patch frames as proof of GUI
  repaint.
- Desktop IPC role discovery advertises `canHandle=true` without an explicit
  request handler, stores request parameter values, or lets a forwarded request
  hang without a response.
- Desktop IPC `turn/start` or `turn/steer` route probing touches the real
  Desktop IPC socket, runs without explicit mutating-method opt-in, or reaches
  a handler that does not send a response.
- Desktop IPC cold-start/normal-turn capture diff stores raw IPC values,
  prompt text, patch values, or secrets; touches the real Desktop socket from
  fixture tests; or treats a method/path diff as verified GUI repaint.
- Outbound watched-run progress notifications are invisible to the next
  Telegram supervisor turn.
- Quiet Telegram FYI mode suppresses alerts or approval prompts, forgets
  watched-run progress context, or reports a suppressed FYI as sent.
- Quiet Telegram FYI mode suppresses watched-run blocker progress such as
  HALTED runs, sandbox-blocked worktree creation, CI failure, or
  approval-needed states.
- Progress-context repair sends a duplicate Telegram notification or writes a
  blank-chat memory row.
- A progress notification grants permission to mutate a target session.
- Daemon restart duplicates already-ingested event lines.
- Run snapshot changes after the run has started.
