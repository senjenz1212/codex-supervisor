# Agent Interaction Snapshot

The Agent Interaction Snapshot is a ledger snapshot derived from persisted
dual-agent gate events. It is not a UI state source, mailbox, app-server
projection, or Desktop repaint mechanism.

The snapshot reads only `dual_agent_gate_round` and
`dual_agent_gate_result` rows from the supervisor SQLite `events` table for a
single run/task. The event ledger remains the source of truth for interaction
state, liveness, blockers, and handoff metadata.

The Agent Interaction Inbox is a ledger-backed inbox projection over the same
rows. It renders rounds, gate results, blockers, warnings, and handoff metadata
as ordered read-only items for operator inspection. It has no ack/read/unread semantics,
cannot mutate gate state, and cannot accept, reject, pause, resume, or override
a dual-agent gate.

External filesystem-style inboxes can still be useful as interaction surfaces,
but not as the authority for this supervisor. The durable boundary remains the
SQLite event ledger plus typed dual-agent payloads; the inbox is only a derived
view.
