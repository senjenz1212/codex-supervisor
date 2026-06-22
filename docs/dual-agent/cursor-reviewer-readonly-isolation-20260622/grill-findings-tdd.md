## TDD Grill Findings

1. The first proof must not call only a private copy helper. Resolution: the first three RED tests drive `invoke_cursor_agent`, the same interface used by supervisor workflow reviewers.
2. The sandbox-options test is necessarily adapter-level, but it must not become the safety proof. Resolution: the safety tests rely on physical isolation and original worktree status checks.
3. The oracle-exclusion test can become superficial if it checks only prompt fields. Resolution: it inspects the isolated reviewer cwd visible to the fake Cursor runner.
4. Full-panel availability is already mostly enforced by reviewer registry behavior. Resolution: keep the integration test focused on the user-visible S_full availability and Codex-only labels, not on duplicating registry unit tests.

All findings are resolved. The plan keeps one RED to one GREEN sequencing and starts at the public reviewer invocation boundary.
