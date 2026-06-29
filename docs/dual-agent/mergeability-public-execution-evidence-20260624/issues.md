# Issues

## Issue 1: Add public execution evidence to full-gate reviewer packets

### PRD Promise

Covers P1, P2, P4, and P5.

### Public Boundary

`run_paired_acceptance_pilot` produces `per_task_results[*].supervisor_full_gate_review.reviewer_packet`.

### Acceptance Criteria

- Reviewer packets include `public_execution_evidence`.
- The block summarizes fixture file overlay, public candidate tests, reverse-classical quality, lint/build, scope/locality, protected-path exclusion, and hidden-oracle exclusion.
- Hidden oracle material does not appear in the packet.
- Existing full-panel report tests remain green.

## Issue 2: Add public execution evidence to SWE-bench reviewer packets

### PRD Promise

Covers P3, P4, and P5.

### Public Boundary

`swebench_mergeability_fixture_runner` writes reviewer packet JSON artifacts.

### Acceptance Criteria

- Reviewer packets include patch-apply receipts.
- Reviewer packets include public-probe command receipts.
- The evidence block avoids forbidden oracle field names and protected content.
- Existing SWE-bench fixture/replay packet tests remain green.

