## Slice 1: Held-Out Task-Class Corpus Coverage

Priority: high

### PRD Promise

P1, P2, and P5.

### Scope:

Extend the mergeability bench report path so held-out task-class coverage is visible in the paired acceptance report. Add deterministic fixture coverage for at least two task classes, validate positive and negative controls per class, and keep hidden oracle labels out of public report fields.

### Acceptance Criteria:

- [ ] `run_paired_acceptance_pilot` reports held-out split and task_class coverage for included tasks.
- [ ] `validate_mergeability_corpus` fails when a held-out task_class lacks either passing or failing controls.
- [ ] The first RED tests exercise `run_paired_acceptance_pilot` and `validate_mergeability_corpus` before helper-level metadata checks.

### Blocked By

Completed by prior slice: full supervisor gate arm and reviewer-panel report fields.

## Slice 2: No-Regression Findings And Replayable Calibration Exports

Priority: high

### PRD Promise

P1, P3, P4, and P5.

### Scope:

Extend paired acceptance reporting so a candidate that breaks a previously passing behavior is marked as a no-regression failure. Export deterministic hashes for held-out coverage and no-regression findings, and preserve calibration-only authority flags so this evidence cannot become an applyable policy proposal.

### Acceptance Criteria:

- [ ] A regression candidate that breaks previously passing behavior is caught and reported as a no-regression failure.
- [ ] Calibration reports remain `metric_applyable=false`, `improvement_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.
- [ ] Report exports include deterministic hashes tying task_class coverage and no-regression findings to the paired report.
- [ ] Policy derivation creates no applyable proposal from held-out no-regression calibration evidence.

### Blocked By

Slice 1 must land first so no-regression findings can reference task_class coverage and held-out split metadata.
