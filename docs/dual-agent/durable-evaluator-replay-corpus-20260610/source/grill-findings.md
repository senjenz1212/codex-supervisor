# PRD Grill Findings

### Finding 1: Reusing the workflow dispatcher command would be the wrong abstraction

Status: resolved

The existing dispatcher launches the dual-agent workflow CLI, while AutoResearch evaluators have a different request and result contract. The implementation should reuse the durable job lane and claim semantics without forcing evaluator jobs through the workflow CLI.

Resolution: add `supervisor/autoresearch/durable_jobs.py` as a narrow adapter over the existing job table and state methods.

### Finding 2: Durable reservation is insufficient without trial progress

Status: resolved

A job row can survive a crash while still losing completed evaluator trials if the evaluator only writes the final artifact. This would make retries expensive and ambiguous.

Resolution: persist `evaluator-runs/<attempt>.progress.json` after each successful trial and reload contiguous progress before starting the next trial.

### Finding 3: Budget and timeout failures must be visible to validators

Status: resolved

Limit failures are not only operational errors; they are evidence quality failures. If budget or timeout is hidden in stderr, a report could look stronger than it is.

Resolution: map budget and timeout errors into execution errors and deterministic validation gaming flags.

### Finding 4: Default evaluator must obey the same hash-pinned contract

Status: resolved

The replay-corpus evaluator is only safe as a default if it uses the same evaluator ref/hash contract as bespoke evaluators.

Resolution: resolve the default evaluator path and sha256 before live execution when no explicit evaluator is supplied.
