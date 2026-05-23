# ADR 0002: Use Current Cortex As The Dual-Agent Cockpit

## Status

Accepted

## Context

Slice 0 for dual-agent delivery requires a cockpit target before probes start.
The cockpit must show durable state: gate outcomes, implementation artifacts,
subagent summaries, PR/SHA/test evidence, blockers, and escalation state.

Codex Desktop is not the truth surface for this flow. Current supervisor
evidence treats Desktop status sync as `history_only`; live GUI repaint is not
a dependency. Telegram is useful for approvals and escalations, but it is not a
good artifact-review surface.

The open choice was:

- wire the current Cortex cockpit now and accept a later strangler-fig path; or
- build a minimal new cockpit for this supervisor flow.

## Decision

Wire the current Cortex cockpit for Slice 0 and later dual-agent milestones.

Use the supervisor ledger as the source of truth. Cortex renders that ledger.
If the current Cortex stack proves too heavy or unreliable for this use case,
replace it behind the same ledger-facing contract later.

## Consequences

- Slice 0 can proceed without inventing another UI.
- Telegram remains focused on approval, escalation, blockers, and shipped
  milestones.
- Codex Desktop remains optional history-only mirror, not an operator source of
  truth.
- Future cockpit replacement is a rendering/backend integration problem, not a
  change to the dual-agent gate protocol.

