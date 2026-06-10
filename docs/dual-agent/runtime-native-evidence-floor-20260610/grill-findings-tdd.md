# TDD Grill Findings

## Finding 1 - First Tests Hit The Workflow Boundary

Resolution: The first three tests use `run_dual_agent_workflow`, not private helper-only assertions. Helper tests may be added after the boundary behavior is pinned.

## Finding 2 - The Acceptance Test Must Use Real Files

Resolution: The positive path initializes a tiny repository and writes actual files so acceptance depends on observed filesystem/git/test state.

## Finding 3 - Runtime Evidence Event Must Be Replayable

Resolution: The transcript test verifies the ledger event, not only the in-memory receipt list.

## Finding 4 - Cursor/Claude Live Calls Stay Out Of Unit Tests

Resolution: Unit tests continue to use fake runners below the public workflow boundary; live Claude Code and Cursor SDK review are enforced by the supervised workflow for this slice, not by local unit tests.
