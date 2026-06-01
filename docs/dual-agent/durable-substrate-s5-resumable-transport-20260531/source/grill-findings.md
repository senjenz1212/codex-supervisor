# PRD Grill Findings: Durable Substrate S5

### Finding 1: The Transport Choice Must Be Evidence-Based

Status: resolved

The PRD now explicitly chooses app-level resync because the repo is currently a
FastMCP stdio server and S1/S2/S3a already provide ledger tail, idempotent
reattach, and durable terminal outcomes. Streamable HTTP is left out of scope
until a concrete deployment requires it.

### Finding 2: Catch-Up Must Be Read-Only

Status: resolved

A catch-up call that writes its own event would perturb the stream and make
exactly-once client replay harder. The PRD now requires the catch-up tool to be
read-only and to return only already committed ledger events.

### Finding 3: Cursor Advancement Must Not Assume Contiguity

Status: resolved

SQLite `AUTOINCREMENT` event ids are monotonic but not a contract for contiguous
pages. The PRD now says clients advance to the maximum returned `event_id` and
never page by arithmetic.

### Finding 4: Reattach And Poll Are Part Of The Same Promise

Status: resolved

Catch-up alone only restores progress events. The PRD now makes reconnect a
three-part protocol: idempotent submit to reattach, catch-up to replay missed
events, and ledger-first poll to read terminal outcomes.

### Finding 5: Documentation Is A Product Requirement

Status: resolved

S5 changes client behavior, so the recovery protocol cannot live only in test
names. The PRD now requires a dedicated reconnect protocol document.

### Finding 6: S5 Must Not Drift Into S3b Or Transport Rewrite

Status: resolved

The PRD now names Streamable HTTP, EventStore, projection rebuilds, and gate
semantics as out of scope for this slice.
