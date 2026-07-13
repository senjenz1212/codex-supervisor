# PRD: OBS-001 Observability Repair

## Problem

The rollout watcher indexes Codex wrapper names instead of nested event kinds,
checks terminal names that real rollouts do not emit, and creates fallback run
IDs unrelated to submitted workflows. Drift then sees runs without task
identity and refuses semantic evaluation unless path violations cross the L1
threshold.

## Solution

Normalize retained raw Claude, Codex, and OpenCode events into one lifecycle
taxonomy; co-register workflow and target-session identities at submission;
route rollout events through that join; and let independent semantic signals
open drift adjudication even when scope violations are zero.

## Promise Contracts

### P1. Nested events reach canonical lifecycle states

- Public boundary: `RolloutWatcher._drain_file`.
- Required kinds: `run.started`, `turn.started`, `tool.started`,
  `tool.completed`, `agent.message`, `turn.completed`, `turn.failed`,
  `run.completed`, `run.failed`, and `run.cancelled`.
- Terminal kinds must update the run and enqueue exactly one `evaluate_run`
  decision.
- Raw source payloads remain stored for replay and diagnosis.

### P2. Submission establishes one workflow/session join

- Public boundary: `CodexSupervisorMcpAPI.submit_dual_agent_workflow_job`.
- Submission resolves an explicit target session first, then target-specific
  environment identity, then a workflow-ID fallback.
- The existing public `State.register_run` API co-registers the workflow
  `run_id` with the target session; no `state.py` schema edit is required.
- A session-keyed sidecar records workflow run, target session, task, target,
  scope, and config metadata before rollout ingestion.
- Same-token retries retain the originally persisted target-session join.

### P3. Semantic drift is independent of path drift

- Public boundary: one `DriftDetector` active-run tick.
- Zero path violations must not suppress L2 goal similarity or triggered L3
  plan progress.
- Scope, low similarity, abandoned/blocked plan, repeated messages, repeated
  tool errors, or time without progress may independently enqueue L4.
- The adjudication evidence names the triggering signals and preserves
  `scope_violations=0` for path-independent cases.

### P4. Tests use captured nested schemas

- Sanitized Codex and Claude Code JSONL captures preserve real wrapper and
  nesting structure.
- Captures contain no prompts, secrets, user paths, or encrypted reasoning.
- OpenCode’s documented event shapes have explicit normalization coverage.

## Non-goals

- Historical backfill or closure of already-stale production rows.
- A new run-state schema or edits to `State.active_runs`.
- Treating raw Telegram callback consumers as canonical-event consumers.
- Solving concurrent workflows sharing one target session; the session sidecar
  intentionally routes to the latest submitted workflow for that session.
