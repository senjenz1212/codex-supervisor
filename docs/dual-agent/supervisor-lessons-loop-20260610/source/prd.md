# PRD: Supervisor Lessons Loop

## Problem Statement

Supervised runs can fail for reasons the supervisor already knows how to classify, but that knowledge currently stays trapped in a single run transcript. Future lead workers can repeat the same missing-test, reviewer-disagreement, drift, or repeated-gate mistakes because there is no replayable cross-run memory that is advisory, scoped, and hashable. The operator needs learning without policy mutation: lessons should warn future agents, not make decisions for them.

## Solution

Add a deterministic lessons loop. At run end, derive lesson records from supervisor-owned evidence: trace-envelope failure taxonomy, reviewer disagreement, repeated gate failures, and drift or probe cohort failures. Store each lesson durably with a stable key. At future gate start, snapshot matching lessons for the routed task class and gate, build a canonical "Known failure modes to verify before claiming" block, inject it into the lead instruction, and write the block hash to the workflow route, handoff packet, transcript metadata, and ledger events. Lessons remain advisory and cannot satisfy probes, reviewer verdicts, runtime receipts, or typed outcomes.

## User Stories

- As an operator, I want a blocked run to produce a durable lesson so the same failure is visible to later runs.
- As a lead worker, I want concise known-failure guidance that matches the current task class and gate.
- As a reviewer, I want the injected lesson block to be hashable and replayable from ledger data.
- As a maintainer, I want lessons to stay below gate authority so they cannot accidentally approve work.

## PRD Promise Contracts

P1. Durable Lesson Records

- User-visible promise: completed supervised runs create replayable lesson records when failure taxonomy, reviewer disagreement, repeated gate failure, or drift/probe cohorts expose a known failure mode.
- Representative action: a workflow blocks at an execution or outcome gate with a failure taxonomy attached to the gate result.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: a lesson row is recorded with `task_class`, `gate`, `taxonomy_code`, `root_cause`, `remediation`, `source_run_id`, and `created_at`; duplicate derivations for the same source run are idempotent.
- Forbidden outcomes: no lesson is written for a failed run; lessons are written only to loose files; lesson storage changes typed gate decisions.

P2. Matching Injection

- User-visible promise: future gates receive a deterministic "Known failure modes to verify before claiming" block when lessons match the current `task_class` and `gate`.
- Representative action: a future workflow starts the same gate and task class after a prior failure lesson exists.
- Public boundary: `dual_agent_lead_invocation`.
- Allowed outcomes: matching lessons are ordered deterministically, injected into the lead instruction, and available in the handoff packet.
- Forbidden outcomes: non-matching lessons leak into unrelated task classes or gates; injection depends on nondeterministic LLM summarization.

P3. Replayable Hashes

- User-visible promise: the injected lesson block can be reconstructed and verified by hash from durable data.
- Representative action: replay reads the lesson-injection event, workflow route snapshot, or handoff packet for a gate.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: supervisor writes a `supervisor_lesson_injection` event containing the block hash, lesson ids, task class, gate, and canonical block text or reconstructable inputs.
- Forbidden outcomes: the hash is missing, unstable, or only present in a transient transcript.

P4. Advisory Only

- User-visible promise: lessons inform the lead but never advance gates, override reviewer panels, or mutate policy defaults.
- Representative action: a matching lesson exists before a future gate.
- Public boundary: `dual_agent_runner`.
- Allowed outcomes: gates still require the existing probes, runtime evidence, typed outcomes, and reviewer decisions.
- Forbidden outcomes: a lesson counts as acceptance evidence; lessons bypass P1/P2/P3/P11/P12/P13/P14; policy defaults are changed.

P5. Run-End Sources

- User-visible promise: lessons come from deterministic supervisor evidence: failure taxonomy, reviewer disagreement, repeated gate failures, and drift/probe cohorts.
- Representative action: a blocked gate result contains trace-envelope failure taxonomy and a Cursor disagreement.
- Public boundary: `supervisor_event_ledger`.
- Allowed outcomes: extraction is deterministic, local, and source-attributed to the run and gate.
- Forbidden outcomes: lessons are hallucinated by a model or inferred from free-form prose without deterministic evidence.

## Implementation Decisions

- Add `supervisor_lessons` as a durable table with stable lesson ids rather than a filesystem index.
- Use exact `task_class` plus `gate` matching; fall back to routed workflow complexity when no explicit dynamic task class exists.
- Build lesson blocks in `supervisor.lessons` with a canonical order and SHA-256 hash.
- Store hashes in workflow route lesson snapshots, handoff packets, interaction metadata, and `supervisor_lesson_injection` events.
- Keep existing gate predicates unchanged; lesson data is metadata and prompt guidance only.

## Testing Decisions

- Start with public-boundary state and lead-invocation tests before helper-only tests.
- Prove run-end idempotency by recording lessons twice from the same failed run.
- Prove matching and non-matching injection behavior with task class and gate fixtures.
- Prove replayability by recomputing the injected block hash from the stored block.
- Re-run workflow, lead, schema migration, and Postgres structural tests because the change touches shared supervisor routes.

## Out Of Scope

- Automatic policy mutation from lessons.
- Letting lessons advance gates or satisfy evidence receipts.
- External vector search or semantic lesson matching.
- Runtime adoption of Temporal, Restate, or DBOS.
- Replacing Cursor, Claude Code, reviewer panel, or runtime-native evidence checks.
