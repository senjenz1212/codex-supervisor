# PRD: Durable Execution Engine Decision

## Problem Statement

Layers 0, 0.5, and 1 give codex-supervisor a working durable job lane:
durable submit, recovery phases, leases, admission control, Postgres
SKIP-LOCKED claimers, and run-partitioned catch-up. The operator now needs a
decision about whether a durable-execution engine should replace any of that
stack, or whether the supervisor should keep borrowing the relevant patterns
without adopting a runtime.

This slice is intentionally report-only. It must not change the default runtime,
flip policy, or move production execution to Temporal, Restate, or DBOS.

## Solution

Produce an ADR under `docs/adr/` that compares four options against the
current stack:

- keep the hand-rolled Layer 0/0.5/1 stack;
- adopt Temporal for the submit-to-workflow lifecycle;
- adopt Restate for idempotent submit/attach ingress;
- adopt a DBOS-style Postgres durable-execution library.

Add a small disabled-by-default Temporal spike seam that models only the
submit-to-workflow lifecycle. The spike uses the idempotency key as the workflow
id, asks the client for `USE_EXISTING` conflict semantics, compares duplicate
submit behavior against the current Layer-0 reservation path, and emits a
report-only comparison. The spike uses fake-client tests and does not require a
live Temporal service.

## User Stories

1. As the operator, I want the ADR to say which engine, if any, should replace
   the hand-rolled job lifecycle, so that the next architecture slice is not a
   runtime adoption by inertia.
2. As a maintainer, I want the decision scored against operational surface,
   code removed, exactly-once submit strength, 100-agent throughput, wider-stack
   fit, and migration blast radius, so that the comparison is auditable.
3. As a maintainer, I want a tiny Temporal submit spike, so that the ADR is
   grounded in executable behavior rather than only prose.
4. As a local developer, I want the default runtime to remain hand-rolled, so
   that ordinary tests and local workflows do not require Temporal, Restate, or
   DBOS.
5. As a reviewer, I want the ADR to name what would be replaced and what would
   stay hand-rolled, so that adoption scope stays bounded.

## PRD Promise Contracts

P1. ADR compares all four options
User-visible promise: The ADR evaluates keep hand-rolled, Temporal, Restate,
and DBOS-style Postgres library options.
Representative prompts or actions: Read
`docs/adr/0004-durable-execution-engine-decision.md`.
Public boundary: documentation and architecture record.
Allowed outcomes: Each option has scores and rationale for the required
criteria.
Forbidden outcomes: A single-engine pitch without scoring or without the
keep-hand-rolled option.
Related user stories: 1, 2

P2. The Temporal spike is disabled by default
User-visible promise: The code has a flagged spike seam, but runtime defaults
remain hand-rolled.
Representative prompts or actions: Instantiate default durable execution config
or run the focused spike tests.
Public boundary: target_config_load and unit tests.
Allowed outcomes: `engine=hand_rolled`; `temporal_spike_enabled=false`.
Forbidden outcomes: Default submit path requires a Temporal service or imports a
Temporal SDK.
Related user stories: 3, 4

P3. The spike models exactly-once submit and reattach semantics
User-visible promise: Duplicate submits with the same idempotency key attach to
the same Temporal workflow handle and compare against the Layer-0 reservation
path.
Representative prompts or actions:
`uv run pytest tests/test_durable_execution_engine_spike.py -q`.
Public boundary: dual_agent_runner behavior model.
Allowed outcomes: The fake Temporal client sees workflow id equal to the
idempotency key and conflict policy `USE_EXISTING`; duplicate submits return the
same handle.
Forbidden outcomes: The spike creates a new workflow id on retry or reports
success without exercising Layer-0 reservation behavior.
Related user stories: 3, 5

P4. The ADR names replace-vs-stay boundaries
User-visible promise: The ADR states which files or responsibilities Temporal
could replace and which supervision boundaries remain in codex-supervisor.
Representative prompts or actions: Read the "What Would Be Replaced" and "What
Stays Hand-Rolled" sections.
Public boundary: documentation and architecture record.
Allowed outcomes: Replacement scope is limited to submit, workflow lifecycle,
retry, leases, and dispatcher responsibilities; gates and audit ledger stay.
Forbidden outcomes: A runtime adoption plan that weakens P1/P2/P3/P13/P14,
reviewer panel, artifacts, or ledger audit.
Related user stories: 1, 5

## Implementation Decisions

- Add a small module under `supervisor/` for the report-only Temporal submit
  spike.
- Add a disabled durable-execution config section rather than changing existing
  submit behavior.
- Use a protocol/fake-client boundary so tests do not import or require a live
  Temporal SDK.
- Keep the ADR as the durable output of this slice.
- Do not alter `mcp_tools/codex_supervisor_stdio.py` submit behavior in this
  slice.

## Testing Decisions

- First RED tests exercise the public behavior model for duplicate submit and
  reattach, not private helper-only parsing.
- Add a config default test proving the hand-rolled runtime remains default.
- Add a fake-client Temporal test proving workflow id and conflict-policy
  arguments.
- Add a comparison test that uses the real SQLite Layer-0 reservation path for
  a handful of tasks.
- Run focused tests, relevant config tests, full suite, and diff checks before
  gate submission.

## Out of Scope

- Replacing the production runtime with Temporal, Restate, or DBOS.
- Adding a live Temporal service dependency.
- Multi-region or HA design.
- Sharding Postgres.
- Changing agentic lead policy, reviewer panel behavior, or gate semantics.
