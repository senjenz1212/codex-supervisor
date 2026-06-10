# Implementation Slices

## Slice 1: Trend Table And Ledger Recorder

Priority: P1
Estimate: M

Scope: Add the persisted trend table, schema migration, state helpers, and run-level recorder for gate metrics.

PRD promises: P1, P4

Public boundary: `quality_trends_record_run`.

Implement a forward migration/table plus state helpers that persist one row per `run_id`/`gate` with task id, task class, accepted flag, first-pass acceptance, revision rounds, and `time_to_accepted_outcome_s`.

Acceptance Criteria:

- [ ] A seeded ledger run persists one row per observed gate.
- [ ] First-pass acceptance, revision rounds, and time-to-accept match the event timeline.
- [ ] SQLite and Postgres state interfaces expose the same trend helper shape.

First RED test: seeded gate-result ledger with accepted and revised gates records correct persisted rows.

## Slice 2: Sampled P11 Audit

Priority: P2
Estimate: M

Scope: Re-verify a bounded accepted execution/outcome sample against runtime evidence and write false-accept counters into the trend row.

PRD promises: P2, P4

Public boundary: `sample_p11_false_accept_audit`.

Use existing runtime evidence semantics to re-check a bounded accepted sample against git/deliverable state and persist false-accept counters using reviewer-panel naming.

Acceptance Criteria:

- [ ] A known accepted-but-missing deliverable is counted as a false accept.
- [ ] The audit stores `false_accept_count`, denominator, rate, and sample details.
- [ ] Audit output is observational and never alters gate decisions.

First RED test: accepted fixture with a missing declared deliverable is counted as one false accept.

## Slice 3: Read-Only Trend Query And CLI

Priority: P3
Estimate: S

Scope: Add a read-only query helper and CLI command for aggregate trend rows filtered by `task_class` and `gate`.

PRD promises: P3, P4

Public boundary: `query_quality_trends` and `scripts/run_supervisor_trend_metrics.py query`.

Expose aggregate rows filterable by task class and gate. Query must not write events or mutate trend tables.

Acceptance Criteria:

- [ ] Query returns only matching `task_class` and `gate` rows.
- [ ] Query emits JSON through the CLI.
- [ ] Event count and trend row count remain unchanged after query.

First RED test: query returns only matching task class/gate rows and event/trend write counts do not change.
