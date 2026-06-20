# TDD Grill Findings

### Finding 1 -- The first proof must cross the runner boundary

Status: resolved

Concern: Testing only a patch helper or command helper would allow the runner to remain shallow and unproven.

Resolution: The first RED test invokes `swebench_mergeability_fixture_runner` and inspects emitted public command receipts plus public worktree isolation.

### Finding 2 -- Freeze ordering must be observable

Status: resolved

Concern: A test that only checks final report shape could miss oracle reads before public decisions are fixed.

Resolution: The TDD plan requires decision artifact hashes and timestamps to precede oracle outcome attachment, making ordering testable through emitted artifacts.

### Finding 3 -- Reviewer unavailable semantics need a boundary test

Status: resolved

Concern: The existing full-gate bridge has often been unavailable, so a fixture runner could accidentally mirror S_probe or treat missing review as accept.

Resolution: The TDD plan includes a no-panel test and an injected-panel disagreement test, both through the runner public boundary.

### Finding 4 -- Report-only invariants need explicit negative evidence

Status: resolved

Concern: A real report file could later be mistaken for an applyable policy result if tests only verify FAR/TAR fields.

Resolution: The TDD plan asserts every report-only invariant remains false and no policy proposal artifacts are created.
