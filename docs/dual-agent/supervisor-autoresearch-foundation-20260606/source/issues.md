# Issues: Supervisor AutoResearch Foundation

## Slice 1: Domain schemas and ledger event orchestration

Priority: P1

Scope: Add `AutoresearchExperiment`, `AutoresearchAttempt`, and
`AutoresearchValidationReport` schemas plus an orchestrator that writes additive
ledger events for experiment, attempt, validation, and report milestones.

PRD promise: P1, P5

Acceptance Criteria:

- [ ] Public orchestrator starts an experiment and writes
  `autoresearch_experiment_started`.
- [ ] Public orchestrator records attempt start and completion with IDs,
  changed files, hashes, metrics, cost, wall clock, and status.
- [ ] Report emission writes `autoresearch_report_emitted`.
- [ ] No schema migration or existing `state.py` table rearchitecture is needed.

## Slice 2: Immutable/mutable surface validation

Priority: P1

Scope: Add deterministic validation that accepts only changed files covered by
mutable paths, rejects immutable authority surfaces, verifies artifact hashes
when possible, and records gaming flags.

PRD promise: P2, P5

Acceptance Criteria:

- [ ] Mutable-only changed files validate successfully.
- [ ] Attempts touching `supervisor/state.py`, gate logic, reviewer aggregation,
  validation code, or evaluator fixtures are rejected.
- [ ] Missing evidence refs or artifact hash mismatches become validation flags.
- [ ] Validator returns evidence only and has no gate-advancement API.

## Slice 3: Repeated-trial metrics and report-only recommendation

Priority: P1

Scope: Add report building that reduces repeated metric trials to median and
IQR, flags unstable trial values, exports deterministic JSON, and never allows
default changes.

PRD promise: P3, P5

Acceptance Criteria:

- [ ] Metric trials reduce to median and interquartile range.
- [ ] Unstable metric values are visible in the validation report.
- [ ] Every report contains `default_change_allowed=false`.
- [ ] Recommendation is textual evidence for operator review, not authority.

## Slice 4: Fixture-only runner and live-call guard

Priority: P2

Scope: Add a script that loads deterministic fixture input, runs the
orchestrator, writes a report, and rejects non-fixture execution unless
`--allow-live` is supplied.

PRD promise: P4

Acceptance Criteria:

- [ ] Fixture replay is the default mode.
- [ ] Non-fixture mode raises without `--allow-live`.
- [ ] Script output includes report JSON and stable report hash.
- [ ] Default path performs no provider, Cursor, target-agent, or subprocess
  workflow execution.

## Slice 5: Compatibility checks for current supervisor authority

Priority: P2

Scope: Add focused tests proving AutoResearch does not alter Cursor reviewer
defaults, production config, agentic policy, dual-agent gates, or reviewer panel
authority.

PRD promise: P5

Acceptance Criteria:

- [ ] Config assertions show Cursor reviewer mode remains compatible.
- [ ] Report-only tests prove `agentic_lead_policy` is not flipped.
- [ ] Existing workflow tests remain green.
- [ ] No code path in AutoResearch writes accepted gate decisions.
