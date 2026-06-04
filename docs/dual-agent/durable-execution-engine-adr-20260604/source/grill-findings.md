# PRD Grill Findings

### Finding 1: "Adopt Temporal" and "decide with a spike" can be confused

status: resolved

Resolved: The PRD now separates the executable spike from production adoption.
The spike is disabled by default, uses a fake client in tests, and does not
change the MCP submit path.

### Finding 2: Restate is strongest at ingress, not at replacing the whole stack

status: resolved

Resolved: The PRD requires the ADR to score Restate as an ingress/attach option
instead of presenting it as a full replacement for the supervisor ledger,
reviewer gates, and dispatcher.

### Finding 3: DBOS overlaps with the Postgres lane

status: resolved

Resolved: The PRD requires a DBOS-style option, but the ADR must compare it
against the already-landed Postgres lane rather than treating it as a greenfield
runtime.

### Finding 4: The ADR must name what stays, not only what goes

status: resolved

Resolved: Promise P4 and the implementation decisions require explicit
replace-vs-stay boundaries. The gates, reviewer panel, audit artifacts, and
ledger evidence remain in codex-supervisor.
