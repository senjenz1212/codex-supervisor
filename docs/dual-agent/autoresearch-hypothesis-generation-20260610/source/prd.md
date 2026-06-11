# PRD: AutoResearch Signal Generator And Gated Runner

## Problem Statement

Supervisor already records failure taxonomy, reviewer disagreement, probe drift,
lessons, evaluator reports, trend metrics, and policy proposal records in the
ledger. The missing product behavior is the first automatic step: recurring
failure signals should become concrete AutoResearch experiments without a human
having to manually notice the pattern and write a fixture. The current workflow
can run evaluator-backed AutoResearch once an experiment exists, but it does not
create inert experiment candidates from repeated evidence or protect immutable
surfaces from accidental execution.

## Solution

Add a ledger-backed AutoResearch experiment queue plus a generator that clusters
existing supervisor signals by task class, gate, and taxonomy code. Once a
cluster crosses a configurable recurrence threshold, the generator writes one
idempotent experiment row with provenance, a replay-corpus evaluator, scoped
mutable paths, trial count, budget, and timeout. Rows start as `draft`, cannot
execute until an operator explicitly activates them, and move to `report_only`
when the implicated surface is immutable. A small runner consumes only
`runnable` rows and invokes the existing live AutoResearch evaluator path so this
slice adds no parallel evaluator execution mechanism.

## User Stories

- As an operator, I want repeated supervisor failures to appear as reviewable
  AutoResearch experiments so I can decide which improvement hypotheses deserve
  budget without hand-authoring each one.
- As a maintainer, I want every generated hypothesis to include provenance event
  ids, source run ids, lesson ids, and implicated paths so I can audit why it
  exists and reproduce the signal cluster.
- As a reviewer, I want immutable or policy-critical surfaces to produce
  report-only rows instead of runnable experiments so automatic discovery cannot
  become automatic policy mutation.
- As a cost owner, I want weekly runnable caps and per-experiment evaluator
  budget and timeout defaults so recurring failures do not create runaway
  experiment execution.

## PRD Promise Contracts

P1. Repeated failure signals draft exactly one inert experiment.
Representative action: write three same-code gate failure events for one task
class and gate, then run the signal generator.
Public boundary: `generate_autoresearch_experiment_drafts`.
Allowed outcome: one queue row with `status=draft`, a stable `signal_key`, and
provenance containing the triggering event ids and source run ids.
Forbidden outcome: below-threshold signals create rows, duplicate generator runs
create duplicate experiments, or provenance is missing.

P2. Operator activation is required before execution.
Representative action: call the auto-runner before and after activating the
draft row through the explicit activation API.
Public boundary: `activate_autoresearch_experiment` and
`run_runnable_autoresearch_experiments`.
Allowed outcome: draft rows produce no report and no workflow job; activated
rows become runnable and execute through the existing live evaluator path.
Forbidden outcome: a draft self-runs, direct subprocess evaluation bypasses the
durable evaluator lane, or activation lacks operator and channel metadata.

P3. Immutable surfaces are report-only.
Representative action: seed recurring failures whose implicated path matches
the immutable path set and run the generator.
Public boundary: `generate_autoresearch_experiment_drafts`.
Allowed outcome: the queue row is `report_only`, includes a stability proposal
pointer, has no mutable paths, and cannot be activated into runnable execution.
Forbidden outcome: immutable files become mutable experiment targets or execute
without human proposal review.

P4. Runnable execution is bounded.
Representative action: activate multiple generated experiments and run the
runner with a weekly cap and fixed clock.
Public boundary: `run_runnable_autoresearch_experiments`.
Allowed outcome: only the allowed number of runnable experiments starts during
the current week, each using configured trial count, budget, and timeout.
Forbidden outcome: the cap is ignored, cost limits are omitted, or status remains
ambiguous after a run failure.

## Implementation Decisions

- Store queue rows in `supervisor_autoresearch_experiments` for both SQLite and
  Postgres lanes, keyed by `experiment_id` and deduplicated by `signal_key`.
- Read signal inputs from existing ledger events and supervisor lesson records;
  the filesystem remains an artifact store, not the source of truth.
- Default evaluator fields come from the replay-corpus reference evaluator and
  remain hash-pinned through the existing AutoResearch schema.
- Keep all generated rows inert by default. The generator never mutates policy,
  never advances gates, and never starts evaluation by itself.
- Use the current `run_autoresearch_fixture(..., execution_mode="live")` path for
  runnable rows so evaluator execution continues through the durable job lane
  introduced before this phase.

## Testing Decisions

The first proof uses public-boundary tests against a real `State` database. The
tests seed ledger events and lesson records, run the generator, inspect queue
rows, activate rows, and assert the durable evaluator path records workflow job
state. Schema tests cover SQLite migration version 9 and the matching Postgres
Alembic migration. Existing AutoResearch, policy evolution, lessons, quality
trend, schema migration, and Postgres lane tests remain part of the focused
regression suite.

## Out Of Scope

This phase does not implement policy overlays, automatic proposal derivation,
rubric hardening, no-mistakes integration, regression rollback drafting, or the
complete-loop two-touchpoint proof. It also does not change gate authority,
reviewer policy, fan-out defaults, immutable path definitions, or
`default_change_allowed=false` report-only invariants.
