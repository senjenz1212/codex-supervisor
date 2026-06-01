# TDD Grill Findings

### Finding 1: First tests must hit the tool workflow boundary

Status: resolved

The TDD plan starts with `run_dual_agent_workflow` and detached submit/catch-up
tests. Helper tests for worker hydration and cleanup are secondary and support
the public boundary promises.

### Finding 2: S2 reattach alone must not be mistaken for fan-out resume safety

Status: resolved

The detached reconnect test asserts both same-job reattach and worker evidence
visibility. The producer hydration test separately proves the worker receipts
are available before fan-out planning.

### Finding 3: Partial resume needs role-aware behavior

Status: resolved

The producer test requires reusing a completed role and spawning only a missing
role. It also states that failed receipts are audit evidence, not completion.

### Finding 4: Orphan cleanup must avoid destructive overreach

Status: resolved

The cleanup test includes stale live, in-budget live, and dead pid records so
the implementation proves it only terminates timed-out live workers.

### Finding 5: Catch-up must see event log facts, not local files alone

Status: resolved

The progress-event test requires worker progress to appear in the event ledger
and catch-up path. P13 remains based on terminal receipt refs and hashes.
