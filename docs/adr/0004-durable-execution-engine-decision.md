# ADR 0004: Durable Execution Engine Decision

## Status

Accepted

## Context

Layers 0, 0.5, and 1 now give codex-supervisor a working durable job stack:
pure durable submit reservations, recovery points, a single SQLite dispatcher
with leases and admission control, an opt-in Postgres lane with DB-enforced
idempotency, SKIP-LOCKED multi-claimer dispatch, and run-partitioned catch-up.

The question is whether a durable-execution engine should replace parts of that
stack, or whether codex-supervisor should keep borrowing the strongest patterns.
This ADR is report-only. It does not move the production submit path onto a new
runtime.

External references checked for this decision:

- Temporal: Workflow ID can be used as the idempotency key; the spike models
  `id_conflict_policy=USE_EXISTING`; activities would host retriable tool calls.
- Restate: HTTP ingress and durable invocation patterns are strong for
  idempotent submit/attach by key.
- DBOS: durable execution is Postgres-backed, which is close to the Layer-1
  lane already introduced in this repository.

## Decision Criteria

Scores use 1 as poor fit and 5 as strong fit for this repository.

| Criterion | Meaning |
|---|---|
| New Operational Surface | Does the option add another always-on service or control plane? Higher means less added surface. |
| Net Code Removed Vs Added | Does adoption remove more local lifecycle code than it adds in adapters, workers, and tests? |
| Exactly-Once Submit Guarantee Strength | Does the option provide server- or DB-enforced duplicate-submit attach semantics? |
| 100-Agent Throughput | Can the option support many long-running jobs without a single SQLite writer ceiling? |
| Wider-Stack Fit | Does the option align with nearby infrastructure and operator knowledge? |
| Migration Blast Radius | Can we adopt it behind a flag without rewriting gates, artifacts, and the reviewer boundary? Higher means lower blast radius. |

## Options

### Option A: Keep Hand-Rolled

Keep Layer 0/0.5/1 as the production implementation. Continue borrowing
patterns from durable engines: idempotency keys, recovery points, leases,
backpressure, partitioned event streams, and attach/resume semantics.

| Criterion | Score | Notes |
|---|---:|---|
| New Operational Surface | 5 | No new service beyond SQLite/Postgres and the dispatcher. |
| Net Code Removed Vs Added | 3 | No code removed, but no adapter/runtime layer added. |
| Exactly-Once Submit Guarantee Strength | 4 | DB unique constraints enforce active idempotency; terminal replay remains local code. |
| 100-Agent Throughput | 4 | Postgres SKIP LOCKED and run-partitioned catch-up are designed for this lane. |
| Wider-Stack Fit | 3 | Fits this repo directly; does not reuse Temporal already present in the broader stack. |
| Migration Blast Radius | 5 | No migration. |

Summary: Best near-term default. It already solves the known submit-drop and
single-writer bottlenecks after Layers 0/0.5/1. The cost is owning lifecycle
code that a durable engine would normally provide.

### Option B: Temporal

Adopt Temporal for the job/workflow lifecycle. Use Workflow ID as the
idempotency key, `USE_EXISTING` conflict semantics for duplicate submit attach,
activities for request write/spawn/tool work, and workflow state for durable
idle/resume.

| Criterion | Score | Notes |
|---|---:|---|
| New Operational Surface | 2 | Requires Temporal server/namespace/workers for this repo, although the wider stack already knows Temporal. |
| Net Code Removed Vs Added | 4 | Could remove much of local reservation, recovery, lease, retry, and dispatcher logic after migration. |
| Exactly-Once Submit Guarantee Strength | 5 | Workflow ID conflict policy is the strongest native fit for submit-drop reattach. |
| 100-Agent Throughput | 5 | Built for many concurrent durable workflows and worker queues. |
| Wider-Stack Fit | 5 | The broader stack already runs Temporal, reducing organizational novelty. |
| Migration Blast Radius | 3 | Can start behind a flag, but adopting activities would touch submit/poll/dispatcher tests and worker packaging. |

Summary: Best replacement candidate if we decide to stop owning lifecycle code.
The adoption should be limited to the job lifecycle first. It should not replace
the supervisor gate contract.

### Option C: Restate

Adopt Restate for idempotent submit/attach ingress and durable invocation. Keep
the supervisor ledger and dispatcher or map the lifecycle to Restate services.

| Criterion | Score | Notes |
|---|---:|---|
| New Operational Surface | 2 | Adds a Restate service/control plane. |
| Net Code Removed Vs Added | 2 | Ingress attach code may shrink, but most supervisor lifecycle and audit code remains. |
| Exactly-Once Submit Guarantee Strength | 5 | Idempotent ingress and attach are highly aligned with the transport-drop problem. |
| 100-Agent Throughput | 4 | Good durable service shape, but the repository would still need worker and audit integration. |
| Wider-Stack Fit | 2 | Less existing local stack fit than Temporal. |
| Migration Blast Radius | 3 | Useful as an ingress layer, but broader adoption would create a second lifecycle model. |

Summary: Strong pattern source for submit/attach. Less compelling than Temporal
as a full replacement because it does not naturally remove as much of the
current dispatcher and audit surface.

### Option D: DBOS-Style Postgres Library

Adopt a DBOS-style durable-execution library on Postgres. Treat the database as
the checkpoint store for workflow state and queues.

| Criterion | Score | Notes |
|---|---:|---|
| New Operational Surface | 4 | No separate workflow service, but the application now depends on a durable-execution library and its schema/runtime contracts. |
| Net Code Removed Vs Added | 2 | Overlaps heavily with Layer 1, so net removal is uncertain. |
| Exactly-Once Submit Guarantee Strength | 4 | Strong when keyed through Postgres constraints/checkpoints. |
| 100-Agent Throughput | 3 | Postgres can scale this lane, but queue partitioning remains a design responsibility. |
| Wider-Stack Fit | 3 | Fits the new Postgres lane, but not as well as Temporal fits the wider stack. |
| Migration Blast Radius | 2 | It would touch the same tables and lifecycle code Layer 1 just stabilized. |

Summary: Good conceptual fit with the Postgres lane, but lowest net value right
now because codex-supervisor already paid the Layer-1 migration cost and still
needs its custom gate/audit surface.

## Spike Result

This slice adds a disabled-by-default spike in
`supervisor/durable_execution_engine_spike.py` and tests it in
`tests/test_durable_execution_engine_spike.py`.

The spike models one path only: submit-to-workflow lifecycle. It sends the
idempotency key as the Temporal workflow id and passes
`id_conflict_policy=USE_EXISTING` through a fake-client-compatible protocol.
The comparison test runs three tasks through both:

- the real SQLite Layer-0 `State.reserve_dual_agent_workflow_job` path;
- the Temporal fake-client spike.

Both systems return one stable handle on duplicate submit and mark retry as a
reattach. The spike report includes `default_runtime_changed=false`.

The spike does not prove live Temporal deployment behavior. It proves the local
adapter shape, exact argument mapping, and comparison contract needed for a
time-boxed follow-up against a real Temporal namespace.

## What Temporal Would Replace

If a later flagged migration adopts Temporal, it should replace only the job
lifecycle surface:

- active workflow-job reservation and duplicate-submit attach logic;
- request-written/spawned recovery-point driving;
- lease heartbeat and stale-lease reaper responsibilities for spawned workers;
- dispatcher claim/backoff/poison handling for workflow job starts;
- parts of `submit_dual_agent_workflow_job` and `poll_dual_agent_workflow_job`
  that exist only to recover transport drops and reattach to durable execution.

The MCP methods should remain thin adapters with the same public names and
response shape until a separate API migration is deliberately chosen.

## What Stays Hand-Rolled

The following stay in codex-supervisor even if Temporal owns job lifecycle:

- P1/P2/P3/P13/P14 probes and the reviewer panel;
- planning artifact validation and skill receipt enforcement;
- handoff packets, artifact export, transcripts, and outcome review;
- audit ledger events that explain gate decisions and reviewer provenance;
- the target-agent adapter boundary and final patch/output capture;
- Postgres event/read models needed for local reporting and historical replay.

Temporal would be a durable execution substrate, not the source of supervisor
truth.

## Migration Cost

A real adoption should be a separate flagged slice with these costs:

- a Temporal worker package and deployment lane;
- workflow definitions for submit-to-terminal lifecycle;
- activity wrappers for request write, CLI worker start, terminal outcome
  capture, and result persistence;
- adapter changes in the MCP submit/poll/catch-up methods;
- compatibility tests that replay existing Layer-0 terminal outcomes;
- operator runbooks for namespace, task queue, retention, retries, and worker
  health.

The likely payoff is deleting or shrinking local dispatcher/recovery code after
the Temporal path proves equivalent. The likely cost is an added service and a
second operational plane.

## No Default Runtime Change

The repository default remains:

```yaml
durable_execution:
  engine: hand_rolled
  temporal_spike_enabled: false
```

No production submit path imports Temporal, calls a live Temporal server, or
changes the MCP workflow-job API in this slice.

## Recommendation

Keep the hand-rolled Layer 0/0.5/1 stack as the production default and keep
borrowing durable-engine patterns. Temporal is the only replacement candidate
strong enough to justify a follow-up spike because it has the best combination
of exactly-once submit semantics, durable idle/resume, worker queue maturity,
and wider-stack fit.

The next slice, if needed, should be a small flagged Temporal pilot for only
submit-to-terminal workflow lifecycle. It should compare:

- duplicate submit attach behavior;
- reattach after client transport drop;
- terminal outcome persistence;
- code deleted or bypassed from dispatcher/recovery logic;
- operator surface added by Temporal.

Do not adopt Restate or DBOS by default now. Keep Restate as an ingress/attach
reference and DBOS as a Postgres-checkpoint reference.
