# TDD Grill Findings: TRACE-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. Tests reject namespace collisions and invalid edge endpoint types.
2. Every required closure break has a negative test.
3. Runtime evidence must descend from the same pinned run.
4. Persistence tests reject UPDATE/DELETE and compare canonical bytes after
   reload.
5. Synthetic graph tests do not replace the integrated TRACER-001 producer
   proof.
