# PRD: Durable AutoResearch Evaluator Execution + Replay-Corpus Evaluator

## Problem Statement

AutoResearch live experiments currently call evaluator execution directly from the orchestrator. That makes an experiment vulnerable to process crashes, transport drops, and repeated trial work because the evaluator attempt is not represented as a resumable job in the supervisor ledger. The system also lacks a useful default evaluator, so hypothesis-driven experiments can accidentally rely on hand-entered fixture metrics instead of executable evidence.

## Solution

Route live AutoResearch evaluator execution through a ledger-backed evaluator job adapter that uses the existing reservation, lease, recovery-point, and terminal outcome primitives. The evaluator runner persists per-trial progress after every successful trial, enforces experiment budget and timeout limits, and returns computed metrics only from hash-pinned evaluator execution. Ship a default replay-corpus evaluator that loads the pinned agentic eval corpus and emits a pass-rate metric from replayable corpus evidence.

## User Stories

- As an operator running an AutoResearch experiment, I can retry after a worker crash and keep completed trial evidence instead of restarting from trial zero.
- As a reviewer, I can inspect the durable job row, evaluator run artifact, and progress artifact to understand exactly what executed.
- As a product engineer, I can run a report-only experiment without writing a bespoke evaluator because the replay-corpus evaluator provides a deterministic default metric.
- As a gate owner, I can trust that AutoResearch remains advisory and cannot mutate policy or advance supervisor gates by itself.

## PRD Promise Contracts

P1. Durable evaluator job boundary

- Public boundary: `run_autoresearch_fixture(..., execution_mode="live")`.
- Promise: live evaluator execution creates and completes a durable job row using the existing job ledger lane.
- Forbidden outcome: the orchestrator directly calls the evaluator without a durable reservation and terminal job outcome.

P2. Trial-level resume

- Public boundary: rerunning the same live attempt with the same output directory after a simulated mid-run crash.
- Promise: completed trial records are loaded from durable progress and only missing trials execute.
- Forbidden outcome: completed trials are rerun, lost, or double-counted.

P3. Budget and timeout limits

- Public boundary: evaluator output, validation report, and gaming flags.
- Promise: `budget_usd` and `timeout_s` overrun attempts are rejected with machine-readable limit flags.
- Forbidden outcome: an over-budget or timed-out evaluator is accepted as candidate evidence.

P4. Replay-corpus default evaluator

- Public boundary: live experiment with empty evaluator ref and hash.
- Promise: the system resolves a hash-pinned local replay-corpus evaluator and emits pass-rate metric trials.
- Forbidden outcome: fixture-entered metrics masquerade as evaluator execution.

P5. Report-only invariants

- Public boundary: report payload and validation record.
- Promise: `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false` remain false.
- Forbidden outcome: AutoResearch applies a policy change, advances a gate, or bypasses reviewer/operator approval.

## Implementation Decisions

- Add an AutoResearch-specific durable evaluator job adapter instead of changing the workflow dispatcher command, because the existing dispatcher is intentionally workflow-CLI specific.
- Store trial progress under `evaluator-runs/<attempt>.progress.json` and final evaluator evidence under `evaluator-runs/<attempt>.json`.
- Use the existing `dual_agent_workflow_jobs` table as the durable job lane so the Postgres SKIP-LOCKED path remains the production multi-claimer mechanism.
- Resolve the default evaluator only when both `evaluator_ref` and `evaluator_hash` are absent; explicit evaluator references keep the existing hash-mismatch failure behavior.
- Keep validators advisory: execution errors produce validation flags and rejected records, never gate advancement.

## Testing Decisions

- Add public-boundary tests around `run_autoresearch_fixture` rather than only helper tests.
- Prove durable dispatch by asserting a terminal job row, request payload, idempotency token, and accepted validation record.
- Prove resume by using a crash-once evaluator with external counters and checking trial zero executes exactly once.
- Prove limit handling with budget and timeout evaluator fixtures that must reject.
- Prove default evaluator behavior with an empty evaluator ref/hash experiment that produces pass-rate metrics.
- Re-run existing AutoResearch, policy evolution, agentic eval corpus, replay CLI, and full pytest suites.

## Out Of Scope

- Automatic policy or prompt mutation from AutoResearch results.
- Replacing the existing supervisor gate sequence.
- Replacing the workflow dispatcher process for dual-agent jobs.
- Adding a new external durable runtime.
- Changing fan-out, reviewer, or supervisor defaults.
