# Implementation Issues

## Slice 1: Durable evaluator job adapter

Priority: P0

Scope: Add an AutoResearch evaluator job adapter that reserves a durable job row, writes a request artifact, claims the job, marks it spawned, and records a terminal result using the existing workflow job lane. This slice maps to PRD promises P1 and P5.

Acceptance Criteria:

- [ ] `run_autoresearch_fixture(..., execution_mode="live")` creates one durable job row for the attempt.
- [ ] The job row records `recovery_point='terminal'` after successful evaluator completion.
- [ ] The request payload identifies `kind='autoresearch_evaluator'`.
- [ ] Report-only invariants remain false.

## Slice 2: Trial progress, resume, and limit handling

Priority: P0

Scope: Persist evaluator progress after each successful trial, reload progress on retry, enforce experiment budget and timeout, and surface limit failures in validation. This slice maps to PRD promises P2 and P3.

Acceptance Criteria:

- [ ] A crash after trial zero can be retried without rerunning trial zero.
- [ ] `budget_exceeded` appears in gaming flags when cumulative evaluator cost exceeds the experiment budget.
- [ ] `timeout` appears in gaming flags when an evaluator subprocess exceeds the configured timeout.
- [ ] Existing mutable-path and hash-mismatch validation still rejects bad attempts.

## Slice 3: Replay-corpus reference evaluator

Priority: P1

Scope: Ship a local default evaluator script that loads the pinned agentic eval corpus, resolves replay evidence paths against the attempt worktree and source root, and emits a pass-rate metric. This slice maps to PRD promise P4.

Acceptance Criteria:

- [ ] Empty evaluator ref/hash resolves to `supervisor/autoresearch/evaluators/replay_corpus.py`.
- [ ] The resolved default evaluator is hash-pinned before execution.
- [ ] The evaluator emits computed pass-rate trials, not fixture-entered numbers.
- [ ] The validation report includes median and IQR statistics.

## Slice 4: Regression and workflow evidence

Priority: P1

Scope: Preserve existing AutoResearch and supervisor behavior by running focused and full regression suites, then submit the implementation through the supervisor rigorous workflow. This slice maps to all PRD promises.

Acceptance Criteria:

- [ ] Focused AutoResearch, policy evolution, replay, and corpus tests pass.
- [ ] The full pytest suite passes.
- [ ] Cursor SDK rigorous review is requested for configured gates.
- [ ] The final report cites a resumed-after-crash run and replay-corpus pass-rate metric.
