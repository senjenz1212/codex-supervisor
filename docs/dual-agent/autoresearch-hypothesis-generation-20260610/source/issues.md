# Issues

## Slice 1: Ledger-Backed Experiment Queue

Priority: P1

Scope: Add a durable `supervisor_autoresearch_experiments` table in SQLite and
Postgres with idempotent `signal_key`, status, experiment payload, attempt
payload, provenance payload, report fields, activation fields, and run tracking.

PRD promise: P1, P2, P3, P4

Acceptance criteria:

- [ ] SQLite forward migration creates the queue table and status index.
- [ ] Postgres inline schema and Alembic migration include the same table and
  index.
- [ ] State APIs can insert or re-read a draft by `signal_key`, list rows by
  status, activate drafts, mark runs started, complete runs, and count weekly
  starts.

## Slice 2: Signal Clustering And Draft Generation

Priority: P1

Scope: Implement the generator that reads failure taxonomy events, reviewer
disagreements, probe cohort flips, and supervisor lessons, then clusters them by
task class, gate, and taxonomy code.

PRD promise: P1, P3

Acceptance criteria:

- [ ] Below-threshold signal clusters produce no rows.
- [ ] Threshold-crossing clusters produce exactly one draft row with replay
  evaluator defaults, scoped mutable paths, configured trials, budget, timeout,
  and provenance event ids.
- [ ] Re-running the generator with the same signals returns no duplicate row.
- [ ] Immutable-surface clusters produce `report_only` rows with proposal
  pointer metadata and empty mutable paths.

## Slice 3: Operator Activation Boundary

Priority: P1

Scope: Add the explicit transition from `draft` to `runnable`, preserving
operator, approval channel, activation time, and report-only immutability.

PRD promise: P2, P3

Acceptance criteria:

- [ ] Draft rows become runnable only through the activation API.
- [ ] Report-only rows remain report-only when activation is attempted.
- [ ] Activation emits ledger evidence that the change was operator-triggered
  and did not mutate policy or advance a gate.

## Slice 4: Gated Auto-Runner With Weekly Cap

Priority: P1

Scope: Add a runner that consumes runnable rows up to the configured weekly cap,
writes a fixture for the existing AutoResearch path, executes live evaluator
runs, records report references and hashes, and parks failed runs with explicit
status.

PRD promise: P2, P4

Acceptance criteria:

- [ ] Draft rows do not execute.
- [ ] Runnable rows execute through the existing live AutoResearch evaluator
  path and write the standard report.
- [ ] Weekly cap prevents excess runnable rows from starting.
- [ ] Completed and failed rows have deterministic terminal statuses and ledger
  events with report-only invariants.
