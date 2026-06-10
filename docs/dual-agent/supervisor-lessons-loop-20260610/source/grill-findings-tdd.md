# TDD Grill Findings

### Finding 1 - Helper-Only Tests Would Miss The Public Boundary

Status: resolved

Risk: pure formatter tests could pass while the lead invocation still misses the block. Resolution: the plan checks `CodexSupervisorMcpAPI` gate-start kwargs, handoff packet fields, and state ledger events.

### Finding 2 - Advisory Semantics Need A Negative Test

Status: resolved

Risk: lessons could become accidental acceptance evidence. Resolution: non-matching and advisory metadata tests assert no receipt-like acceptance is created by injection.

### Finding 3 - Replay Needs Stored Inputs, Not Just Hashes

Status: resolved

Risk: a hash without text or lesson ids cannot be audited. Resolution: tests require block text, lesson ids, and hashes in workflow snapshot, handoff packet, and injection events.

### Finding 4 - Idempotency Is Required At Run End

Status: resolved

Risk: retry/catch-up could duplicate lesson records. Resolution: the failed-run test invokes recording twice and expects one durable row.

### Finding 5 - Cursor Review Must Remain Independent

Status: resolved

Risk: known-failure guidance could be used to bypass reviewer panels. Resolution: the slice keeps reviewer gates unchanged and includes workflow driver regression tests.
