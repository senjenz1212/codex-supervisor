# TDD Grill Findings

### Finding 1

Status: resolved

Concern: Helper-only tests could miss the read-only operator boundary.

Resolution: The TDD plan includes both direct API tests and a CLI JSON query test.

### Finding 2

Status: resolved

Concern: The P11 audit could become vacuous if it only replays existing accepted receipts.

Resolution: The audit test must create a known false accept by deleting or omitting a declared deliverable from git state and asserting `false_accept_count=1`.

### Finding 3

Status: resolved

Concern: Observational-only behavior is easy to claim but hard to prove.

Resolution: The TDD plan includes an explicit invariant test that trend metrics do not emit gate-result events or mutate workflow steps.
