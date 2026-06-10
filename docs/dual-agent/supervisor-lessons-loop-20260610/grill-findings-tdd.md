# TDD Grill Findings

## Findings

### TG1 - Helper-Only Tests Would Miss The Public Boundary

Resolution: T2 and T5 assert through handoff/lead-invocation and ledger events, not just pure formatting helpers.

### TG2 - Advisory Semantics Need A Negative Test

Resolution: T6 proves injected lessons do not count as gate acceptance evidence.

### TG3 - Replay Needs Stored Inputs, Not Just A Hash

Resolution: T4/T5 require the ledger event to store canonical block text and lesson ids so replay can reconstruct and verify.

### TG4 - Idempotency Is Required At Run-End

Resolution: T1 reruns the lesson hook and expects one record, preventing durable retry duplication.

### TG5 - Cursor Review Remains Independent

Resolution: regression coverage keeps Cursor reviewer defaults enabled and does not let lessons bypass reviewer panel decisions.

## Translation Audit

- Every PRD promise P1-P5 has an issue claimant.
- Every issue names a public boundary.
- The first RED tests exercise state, lead invocation, or runner boundaries before helper-only tests.
- Forbidden outcomes around gate bypass, mutable snapshots, and replay calling live agents remain out of scope or explicitly guarded.
