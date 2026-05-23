# Agent Interaction Snapshot

The Agent Interaction Snapshot is a ledger snapshot derived from persisted
dual-agent gate events. It is not a UI state source, mailbox, app-server
projection, or Desktop repaint mechanism.

The snapshot reads only `dual_agent_gate_round` and
`dual_agent_gate_result` rows from the supervisor SQLite `events` table for a
single run/task. The event ledger remains the source of truth for interaction
state, liveness, blockers, and handoff metadata.
