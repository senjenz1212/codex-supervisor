# Agent Supervisor PRD

## Problem Statement

Sam needs a local supervisor that can watch long-running coding-agent work,
catch drift or risky behavior early, and keep a replayable audit trail of what
happened. The immediate agent to supervise is Claude Code. The design must not
paint the product into a Claude-only corner, because Codex may become a future
target.

Today the scaffold is useful but Codex-shaped. It tails Codex rollout files,
posts Codex hook payloads into a FastAPI server, and injects steering with a
Codex resume command. That makes the core supervision idea harder to reuse for
Claude Code and harder to test as a control system.

## Solution

Build a target-agnostic local control plane. Claude Code becomes the v1 target
through `ClaudeCodeAdapter`. Codex remains a future target through
`CodexAdapter`. The supervisor core consumes only normalized events, scope
contracts, hook decisions, actions, and decision-runtime verdicts.

The system remains safety-first: detect, then recommend, then act only when a
capability is explicitly promoted to an enforcing mode. Destructive recovery
actions always require fresh user approval.

## User Stories

1. As Sam, I want Claude Code to be supervised first, so that the design solves
   my current workflow instead of waiting for Codex integration.
2. As Sam, I want Codex kept as a future target, so that I do not have to throw
   away the supervisor when Codex becomes the right surface.
3. As Sam, I want target-specific files and commands hidden behind adapters, so
   that drift detection and replay do not care which agent produced the run.
4. As Sam, I want the supervisor to receive hook events from Claude Code, so
   that risky or off-task actions can be checked in real time.
5. As Sam, I want permission requests treated as first-class events, so that
   escalation and approval state are visible in the audit trail.
6. As Sam, I want the supervisor to capture a run-start scope contract, so that
   drift detection has a stable definition of in-scope work.
7. As Sam, I want never-touch patterns enforced across all targets, so that
   secrets and credentials are protected by default.
8. As Sam, I want each capability to have an operating mode, so that I can
   shadow-test before allowing automated actions.
9. As Sam, I want Telegram to show short recommendations and approval prompts,
   so that I can supervise from my phone.
10. As Sam, I want stale Telegram button presses rejected, so that old approvals
    cannot accidentally trigger actions.
11. As Sam, I want steering to go through the target adapter, so that Claude
    Code and Codex can use different resume mechanisms safely.
12. As Sam, I want one pending steering injection per session, so that the
    supervisor does not create competing corrective turns.
13. As Sam, I want every expensive LLM decision to be replayable, so that I can
    audit why the supervisor warned, nudged, or blocked.
14. As Sam, I want drift verdicts to cite event IDs, so that I can inspect the
    exact evidence quickly.
15. As Sam, I want post-run evaluations to be advice rather than truth labels,
    so that model judgments do not silently become ground truth.
16. As Sam, I want decision labels stored separately, so that I can tune drift
    thresholds from human-reviewed outcomes.
17. As Sam, I want the daemon to recover safely after restart, so that tailing
    does not duplicate or skip event input.
18. As Sam, I want secrets redacted before persistence and notifications, so
    that the audit trail does not become a liability.
19. As Sam, I want the supervisor to degrade safely when Telegram, hooks, or
    resume commands fail, so that failure does not become silent automation.
20. As Sam, I want local markdown issue slices with public-boundary tests, so
    that AFK agents can implement the work without translating the plan again.

## PRD Promise Contracts

### P1. Claude Code Is the First Target

User-visible promise: A user can configure the supervisor for `claude_code` and
run the daemon without any Codex session directory or Codex CLI dependency.

Representative prompts or actions: Set `target.kind: claude_code`, start the
daemon, send a Claude Code hook payload to the hook server.

Public boundary: `target_config_load` and `target_adapter_conformance`.

Allowed outcomes: Claude Code adapter starts; missing Codex config is ignored;
unsupported Claude Code features return honest degraded states.

Forbidden outcomes: startup fails because Codex config is missing; core logic
imports Codex-only payload fields; Claude Code behavior is implemented by
special-casing drift or replay internals.

Related user stories: 1, 3, 4, 19.

### P2. Codex Remains a Future Adapter

User-visible promise: Codex support can be added or exercised through
`CodexAdapter` without changing drift detection, replay, action policy, or
decision-runtime code.

Representative prompts or actions: Run adapter conformance tests with a fake
Codex rollout event and fake resume command.

Public boundary: `target_adapter_conformance`.

Allowed outcomes: Codex adapter is stubbed or partially implemented behind the
same interface; unsupported features report `not_supported`; core behavior does
not branch on target except through adapter selection.

Forbidden outcomes: Codex-specific rollout parsing inside drift detection;
hardcoded `codex exec` calls outside the adapter; replacing the adapter boundary
with a second supervisor loop.

Related user stories: 2, 3, 11.

### P3. Hooks Are Normalized and Audited

User-visible promise: Claude Code hook events are accepted by the HTTP hook
server, normalized into target-independent events, persisted, and answered
according to the configured mode.

Representative prompts or actions: POST a `PreToolUse` or `PermissionRequest`
Claude Code sample payload to `/hook/claude-code`.

Public boundary: `hook_http_api`.

Allowed outcomes: event is stored with redacted raw payload, normalized event is
available for downstream logic, response shape is valid for the hook source.

Forbidden outcomes: raw hook payload is dropped; hook response blocks in shadow
mode; permission requests are treated as generic unknown events.

Related user stories: 4, 5, 8, 18.

### P4. Mode-Aware Detect Recommend Act

User-visible promise: Each capability honors `off`, `shadow`, `advise`, and
`enforce`, so the supervisor never acts on a detector that has not been promoted.

Representative prompts or actions: Configure drift in `shadow`, run a drift
fixture that recommends a nudge, then configure drift in `enforce` and replay
the same fixture.

Public boundary: `mode_policy`.

Allowed outcomes: shadow logs would-do actions only; advise sends recommendations
only; enforce creates actions only when the capability allows it; destructive
actions still require approval.

Forbidden outcomes: shadow injects steering; advise denies hook actions;
enforce bypasses approval for kill or restart; mode is read only at startup when
the replay snapshot says otherwise.

Related user stories: 8, 9, 12, 19.

### P5. Immutable Run Snapshots and Scope Contracts

User-visible promise: Every run captures a frozen config snapshot, target
metadata, scope contract, and tail offset state so drift and replay use the same
input later.

Representative prompts or actions: Register a run with allowed paths and
protected paths, ingest events, restart daemon, replay the run.

Public boundary: `run_registration_api` and `event_ingestion_api`.

Allowed outcomes: snapshot is written once; replay uses the stored scope;
tailing resumes from a persisted offset; never-touch patterns are always present.

Forbidden outcomes: live config changes rewrite old scope; daemon restart
duplicates already-ingested events; protected path writes are treated as benign.

Related user stories: 6, 7, 13, 17.

### P6. Secrets Are Redacted Before Persistence or Notification

User-visible promise: Secrets in hook payloads, tool args, file contents, or
model output are redacted before SQLite storage and Telegram delivery.

Representative prompts or actions: Send a hook payload containing
`ANTHROPIC_API_KEY=secret` or `Authorization: Bearer token`.

Public boundary: `redaction_pipeline`.

Allowed outcomes: stored and sent payloads contain redaction markers; the
original secret is not recoverable from SQLite or Telegram payload text.

Forbidden outcomes: secret appears in `events`, `hook_requests`, `actions`,
`verdicts`, or Telegram message text.

Related user stories: 7, 18, 19.

### P7. Replay Proves Decisions at the Public Boundary

User-visible promise: Given a saved run snapshot and event fixture, a developer
can reproduce drift decisions and action recommendations without live Claude
Code, Codex, Telegram, or model APIs.

Representative prompts or actions: Run the replay entrypoint on a fixture that
contains out-of-scope writes and compare the emitted verdict to an expected JSON
artifact.

Public boundary: `replay_cli`.

Allowed outcomes: replay returns deterministic normalized events, scope
evaluation, mode decisions, and recorded verdict fixtures.

Forbidden outcomes: replay calls live Telegram, live target adapters, or live
LLM APIs by default; replay depends on current config instead of run snapshot.

Related user stories: 13, 14, 16, 20.

### P8. Steering and Recovery Go Through Approved Actions

User-visible promise: Steering, kill, restart, and recovery actions are recorded
as actions, routed through the target adapter, and gated by fresh approval when
destructive.

Representative prompts or actions: Simulate hard divergence, tap a Telegram
approval, then execute or cancel the resulting action.

Public boundary: `action_executor`.

Allowed outcomes: one pending injection per session; stale approvals expire;
target adapter returns delivered or degraded result; destructive actions fail
closed when Telegram is unavailable.

Forbidden outcomes: direct subprocess calls outside target adapters; duplicate
steering injections race; old Telegram callback performs a new action; crash
recovery kills a process without approval.

Related user stories: 9, 10, 11, 12, 19.

## Implementation Decisions

- Introduce `TargetAgentAdapter` with Claude Code as the first production
  implementation and Codex as a future-compatible implementation.
- Introduce `NormalizedEvent`, `HookEvent`, `TargetAction`, `ScopeContract`, and
  `TargetHealth` as stable supervisor-core types.
- Move Codex-specific resume and rollout logic out of core supervision paths and
  behind `CodexAdapter`.
- Add target-aware config with `target.kind: claude_code` as the default.
- Add immutable run snapshots, hook request auditing, action ledger, decision
  labels, and persistent tail offsets.
- Keep Claude Agent SDK as the default `DecisionRuntime`, not as the target
  integration layer.
- Add a replay entrypoint before enabling enforcement.
- Keep destructive recovery actions approval-gated forever.

## Testing Decisions

- First tests for every PRD promise must exercise public boundaries listed in
  `docs/testing/public-boundaries.md`.
- Mocks are allowed below the public boundary for external systems such as
  Telegram, model APIs, and target-agent CLIs.
- Tests must not mock away adapter selection, hook normalization, mode policy,
  snapshot use, action gating, or replay finalization.
- Regression fixtures should preserve the exact raw hook payload or event
  sequence that revealed a bug, with secrets replaced by safe sentinel values.
- Helper tests may follow only after the public-boundary RED test exists.

## Out of Scope

- A polished desktop UI.
- Multi-user or multi-machine supervision.
- Full Codex app-server integration in v1.
- Local Hermes runtime completion beyond a stub and interface contract.
- Fully automated destructive actions without user approval.

## Further Notes

The repo can keep the `codex-supervisor` name while the product becomes agent
supervisor internally. Renaming the repo is not required for v1 and would add
noise before the adapter boundary is proven.
