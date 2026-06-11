# TDD Grill Findings

### Finding 1: Tests Must Hit Public Boundaries

Status: resolved

Concern: Helper-only tests could pass while the real MCP gate-start and policy-evolution flows remain disconnected.

Resolution: The TDD plan includes gate-start instruction construction, MCP stdio policy-evolution tools, quality trend record/query, and lesson query helpers.

### Finding 2: Rollback Tests Must Prove No Mutation

Status: resolved

Concern: A test that only checks for a rollback draft might miss an accidental direct overlay change.

Resolution: The regression test asserts the overlay file contents remain unchanged after verification.

### Finding 3: Lesson Fold Tests Must Preserve Recurrence Signal

Status: resolved

Concern: Folding near-duplicate rows could hide repeated failures from AutoResearch signal generation.

Resolution: Lesson tests assert a folded row records increased observation count, and AutoResearch generator tests assert recurring folded lessons still draft experiments.

### Finding 4: Audit Scheduler Must Stay Observational

Status: resolved

Concern: A scheduled P11 audit could accidentally become a hidden gate decision.

Resolution: Scheduler tests assert audit rows/events are written while gate outcomes and policy mutation flags remain unchanged.
