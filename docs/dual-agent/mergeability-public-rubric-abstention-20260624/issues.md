# Issues

## Issue 1: Add Public Mergeability Rubric To Reviewer Packets

PRD promise: P1, P2.

Public boundary for first RED test: `run_paired_acceptance_pilot` full-gate reviewer packet.

Acceptance criteria:

- Packet includes `mergeability_rubric/v1`.
- Allowed labels include `mergeable`, `not_mergeable`, and `needs_human_review`.
- Rubric is clean under existing public packet leak scanning.

Blocked by: None.

## Issue 2: Treat Needs-Human-Review As Abstention

PRD promise: P3, P4.

Public boundary for first RED test: configured reviewer panel through `run_paired_acceptance_pilot`.

Acceptance criteria:

- Reviewer result label `needs_human_review` is recorded.
- Full gate does not accept rows where the only decisive reviewer abstains.
- FAR/TAR does not count abstention as accepted.

Blocked by: Issue 1.

## Issue 3: Report Rubric Coverage

PRD promise: P5, P6.

Public boundary for first RED test: `run_paired_acceptance_pilot` report.

Acceptance criteria:

- Report includes label counts, labeled coverage, and abstention coverage.
- Coverage block states rubric labels are descriptive and deterministic oracle scoring remains the authority.

Blocked by: Issue 2.
