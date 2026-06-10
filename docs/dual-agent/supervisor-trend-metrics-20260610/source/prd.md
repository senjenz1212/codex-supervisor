# Supervisor Quality Trend Metrics PRD

## Problem Statement

The supervisor now records rich gate, reviewer, and runtime-evidence events, but operators still lack a durable, read-only way to answer whether quality is improving over time. Today, first-pass acceptance, revision pressure, time-to-accept, and false-accept audits require ad hoc event scans or transcript reading. That makes program health hard to compare by task class and by gate.

## Solution

Add a ledger-derived quality trend layer that records compact per-run/per-gate metrics into a persisted table after workflow execution, exposes a read-only query grouped by `task_class` and `gate`, and keeps sampled P11 false-accept audits as observational evidence. The implementation should derive metrics from existing `dual_agent_gate_result`, `dual_agent_workflow_route`, and `dual_agent_runtime_evidence` events rather than asking agents to self-report quality.

## User Stories

- As an operator, I can ask whether a task class is improving by checking first-pass acceptance and revision-round trends without rereading every transcript.
- As a supervisor maintainer, I can audit accepted runtime evidence samples against git state and see a measured `false_accept_rate` using the existing reviewer-panel vocabulary.
- As a gate reviewer, I can trust that trend metrics are advisory telemetry only and cannot advance, block, or mutate any workflow gate.

## Goals

- Persist per-run, per-gate quality metrics derived from the existing ledger.
- Expose a read-only trend query grouped by `task_class` and `gate`.
- Reuse the existing `time_to_accepted_outcome` and `false_accept_rate` vocabulary.
- Add a sampled P11 audit that re-verifies accepted deliverable/runtime receipts against current git state and records caught false accepts.
- Keep metrics observational only: metrics must never gate, block, or mutate policy.

## PRD Promise Contracts

P1. Persist Gate Trend Metrics

The system persists one trend row per observed run/gate with accepted status, first-pass acceptance, revision rounds, and `time_to_accepted_outcome_s`, derived from the ledger rather than from agent claims.

P2. Sampled P11 False-Accept Audit

The system can re-verify a bounded sample of accepted execution/outcome evidence against runtime/git state and record `false_accept_count`, `false_accept_denominator`, and `false_accept_rate`.

P3. Read-Only Trend Query

The system exposes a read-only query, including a CLI path, that filters trend aggregates by `task_class` and `gate` without inserting events or mutating metric rows.

P4. Observational-Only Metrics

Metric recording, querying, and audit values remain telemetry only; no metric result can advance gates, block gates, override reviewers, or mutate supervisor policy.

## Implementation Decisions

- Store trend rows in a dedicated `supervisor_quality_trends` table through the normal schema migration path.
- Compute run metrics incrementally at workflow result assembly and through an explicit record command, avoiding full-history recompute on every query.
- Use `dual_agent_workflow_route.lesson_task_class` where present, then fallback task-class labels, so trend grouping matches the lessons/task-class vocabulary.
- Reuse `collect_runtime_evidence` for sampled P11 audit checks, keeping runtime-native git/test evidence as the floor for false-accept detection.
- Keep SQLite and Postgres state interfaces aligned because the trend table is read by production and local lanes.

## Testing Decisions

- Seed ledger fixtures directly for first-pass acceptance, revision-round, and time-to-accept math.
- Build a temporary git repository with an accepted missing deliverable to prove the sampled P11 audit catches a known false accept.
- Assert query read-only behavior by comparing event and trend row counts before and after query calls.
- Exercise the CLI query path as JSON output and confirm it does not write trend rows.
- Assert metrics do not write new `dual_agent_gate_result` events or alter gate authority.

## Out of Scope

- No policy/default changes.
- No reviewer-panel replacement.
- No full-history recompute on every read.
- No background scheduler in this slice.
- No network or provider calls for metrics.
