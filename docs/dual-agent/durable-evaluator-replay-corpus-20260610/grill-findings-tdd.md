# TDD Grill Findings

### Finding 1: Public boundary tests must drive the orchestrator

Status: resolved

Helper-only tests would miss the original direct-call problem in the orchestrator. The first durable dispatch and resume tests need to call `run_autoresearch_fixture`.

Resolution: the TDD plan names public-boundary tests that inspect report records, ledger events, and durable job rows.

### Finding 2: Resume proof needs a negative counter

Status: resolved

It is not enough to observe a final metric after retry; the test must prove trial zero did not execute twice.

Resolution: the crash-resume test uses external trial counters and asserts trial zero remains at one execution while the failed trial retries.

### Finding 3: Replay-corpus evaluator should stay deterministic

Status: resolved

The default evaluator must not call live models or target agents, because AutoResearch defaults should be cheap and reproducible.

Resolution: the evaluator loads the pinned agentic eval corpus and resolves replay evidence paths instead of invoking live providers.
