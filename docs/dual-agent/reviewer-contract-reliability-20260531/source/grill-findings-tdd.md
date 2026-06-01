# Reviewer Contract Reliability TDD Grill Findings

## Findings

### Finding TG1

status: resolved
severity: high
question: Does the first RED test exercise the real failure boundary?
resolution: Yes. The first implementation RED test targets
`invoke_cursor_agent`, the public boundary that currently returns
`reviewer_contract_unmet` instead of a valid reviewer outcome.

### Finding TG2

status: resolved
severity: high
question: Do helper tests hide workflow semantics?
resolution: No. Helper-level structured-output tests are paired with workflow
tests that preserve valid reviewer rejection blocking and reviewer-unavailable
recovery.

### Finding TG3

status: resolved
severity: high
question: Can tests pass by loosening the contract?
resolution: No. The TDD plan requires reuse of `evaluate_outcome_fidelity` and
the critical-review completeness checker.

### Finding TG4

status: resolved
severity: medium
question: Will live model access be required for the suite?
resolution: No. Live probes are committed as evidence, while tests mock model
I/O below the reviewer invocation boundary.

### Finding TG5

status: resolved
severity: high
question: Does the TDD plan pin the explicit preserve items that blocked the
gate?
resolution: Added named tests for Cursor SDK compatibility routing, the
read-only git-status guard, structured-output truncation, deny blocking parity,
and reviewer-after-Claude/non-Claude default lineage.

## Decision

No open TDD grill findings remain.
