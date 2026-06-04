# Issues: Durable Execution Engine Decision

## Slice ISS-1: Add a disabled Temporal submit lifecycle spike

Priority: P1

Scope:
- Add a small report-only module that models Temporal submit with workflow id
  equal to idempotency key and conflict policy `USE_EXISTING`.
- Add a durable execution config section whose defaults keep the current
  hand-rolled runtime.
- Do not wire this module into the production MCP submit path.

Acceptance criteria:
- [ ] Default durable execution config is `engine=hand_rolled` and
  `temporal_spike_enabled=false`.
- [ ] A disabled spike raises a clear error instead of silently running.
- [ ] An enabled fake-client spike passes workflow id, task queue, payload, and
  conflict policy through the client boundary.
- [ ] No live Temporal SDK or service is required.

PRD promise: P2, P3

Public boundary for first RED test: `dual_agent_runner` behavior model and
`target_config_load`.

## Slice ISS-2: Compare the spike against the Layer-0 reservation path

Priority: P1

Scope:
- Add a test/report helper that runs duplicate submit attempts for a handful of
  tasks through both the fake Temporal spike and the real SQLite Layer-0
  `reserve_dual_agent_workflow_job` path.
- Emit report-only rows with exactly-once and reattach results.

Acceptance criteria:
- [ ] Duplicate Layer-0 reservation calls with the same idempotency key return one
  job id and a reattach on retry.
- [ ] Duplicate Temporal fake-client calls with the same idempotency key return one
  workflow id and a reattach on retry.
- [ ] The report states `default_runtime_changed=false`.

PRD promise: P2, P3

Public boundary for first RED test: `dual_agent_runner`.

## Slice ISS-3: Publish the durable-execution engine ADR

Priority: P1

Scope:
- Create `docs/adr/0004-durable-execution-engine-decision.md`.
- Score keep hand-rolled, Temporal, Restate, and DBOS-style Postgres options.
- Recommend either a runtime adoption path or a keep-borrowing path.
- Name what would be replaced, what stays hand-rolled, migration cost, and
  explicit no-default-change status.

Acceptance criteria:
- [ ] ADR includes the six required scoring criteria.
- [ ] ADR includes the small spike result and its limits.
- [ ] ADR names the recommended next step and states no runtime adoption occurs in
  this slice.

PRD promise: P1, P4

Public boundary for first RED test: documentation artifact.

## Slice ISS-4: Gate and regression validation

Priority: P1

Scope:
- Run focused tests for the spike.
- Run config loading tests that exercise the new default.
- Run the full suite and diff check.
- Submit through the durable supervised workflow with skill receipts and
  planning artifacts.

Acceptance criteria:
- [ ] Focused tests pass.
- [ ] Full suite is green or any unavailable live dependency is explicitly skipped
  by existing test guards.
- [ ] Gate accepts through outcome review with reviewer panel accept or minor-only.

PRD promise: P1, P2, P3, P4

Public boundary for first RED test: test runner and supervised workflow.
