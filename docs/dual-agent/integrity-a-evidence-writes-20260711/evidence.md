# INTEGRITY-A Evidence

## Behavior Proof

- Conflicting completion raised `workflow job terminal outcome discrepancy: proof-job`.
- The appended event kind was `dual_agent_workflow_terminal_discrepancy`.
- The event preserved the accepted original and blocked conflicting outcome; the stored job stayed accepted.
- A symlinked `.supervisor` parent raised `PolicyOverlayError` before any write.
- Approval tests also rejected a symlinked final overlay file and rollback directory with no outside bytes or approval event.

## Planning Validation

- validator: `1.1.0`
- verdict: `accepted`
- rubric: `0.847473` at threshold `0.6`
- all deterministic PRD, issue, TDD, grill, plan, and traceability checks passed

## Tests

```text
84 passed in 3.21s
```

Command: focused INTEGRITY-A, schema, policy-evolution, policy-overlay, and quality-trend test files.

```text
6 passed, 161 deselected in 0.67s
```

Command: focused terminal-completion regressions in `tests/test_dual_agent_workflow_driver.py`.

Python compilation and `git diff --check` passed. Ruff was unavailable because it is not installed or declared in the repository development dependencies.
