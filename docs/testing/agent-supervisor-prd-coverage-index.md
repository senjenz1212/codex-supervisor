# Agent Supervisor PRD Coverage Index

Source PRD: `docs/prd/agent-supervisor-prd.md`

| Promise | Coverage | Issue |
|---|---|---|
| P1 Claude Code Is the First Target | Covered | `.scratch/agent-supervisor/01-target-adapter-foundation.md` |
| P2 Codex Remains a Future Adapter | Covered | `.scratch/agent-supervisor/01-target-adapter-foundation.md`, `.scratch/agent-supervisor/07-codex-compatibility-adapter.md` |
| P3 Hooks Are Normalized and Audited | Covered | `.scratch/agent-supervisor/03-hook-server-claude-code.md` |
| P4 Mode-Aware Detect Recommend Act | Covered | `.scratch/agent-supervisor/03-hook-server-claude-code.md`, `.scratch/agent-supervisor/06-actions-steering-recovery.md` |
| P5 Immutable Run Snapshots and Scope Contracts | Covered | `.scratch/agent-supervisor/02-state-snapshots-redaction.md` |
| P6 Secrets Are Redacted Before Persistence or Notification | Covered | `.scratch/agent-supervisor/02-state-snapshots-redaction.md` |
| P7 Replay Proves Decisions at the Public Boundary | Covered | `.scratch/agent-supervisor/05-replay-harness.md` |
| P8 Steering and Recovery Go Through Approved Actions | Covered | `.scratch/agent-supervisor/06-actions-steering-recovery.md` |

## Integration Scenario Issues

- `.scratch/agent-supervisor/04-drift-cascade-normalized-events.md` composes
  P4, P5, and P7 because drift depends on normalized events, scope contracts,
  modes, and replayable evidence.

## Deferred

- Full Codex app-server integration is deferred. The v1 issue only preserves
  adapter compatibility and resume-command probing.
- Full Local Hermes runtime is deferred. Decision runtime remains interface-first.
