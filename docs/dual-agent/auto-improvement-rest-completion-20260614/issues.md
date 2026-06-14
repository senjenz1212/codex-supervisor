# Issues

## I1: Add AutoResearch experiment parking
- PRD promises: P1
- First RED boundary: `codex-supervisor-axi experiments park <experiment_id> --reason ...` changes queue row status to `parked`, emits an audit event, and parked rows are excluded from draft capacity.
- Tests:
  - AXI park command changes status and emits `autoresearch_experiment_parked`.
  - Parked row is not runnable and cannot be activated.
  - Open draft capacity ignores parked rows.

## I2: Add task-class policy overlay slots and freeze
- PRD promises: P2, P3
- First RED boundary: `_workflow_gate_start_kwargs(... lesson_task_class="source_change")` appends only matching task-class overlay guidance and records scoped hashes.
- Tests:
  - Matching task_class receives scoped overlay guidance.
  - Non-matching task_class does not receive scoped guidance.
  - Frozen task_class suppresses overlay guidance and records `overlay_frozen=true`.

## I3: Require empty-floor evidence for applyable policy proposals
- PRD promises: P4
- First RED boundary: policy proposal derivation from an accepted AutoResearch report without empty-floor comparison creates no applyable proposal.
- Tests:
  - Missing empty-floor comparison blocks applyable proposal.
  - Negative or equal delta blocks applyable proposal.
  - Positive evaluator-derived delta preserves human approval path and no automatic mutation.

## I4: Add observational empty-floor rebaseline cadence
- PRD promises: P5
- First RED boundary: daemon tick over live overlay rows emits observational `policy_empty_floor_rebaseline_due` events only.
- Tests:
  - Due overlay emits rebaseline event.
  - Event is observational and does not mutate overlay.

## I5: Report D1/D2 sufficiency rather than deciding prematurely
- PRD promises: P6
- First RED boundary: `trends` response can state insufficient samples for format and era decisions.
- Tests:
  - zero TOON samples yields `format_decision_status=insufficient_data`.
  - weak MCP denominator yields `transport_decision_status=insufficient_data`.

