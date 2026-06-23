# Issues

## Issue 1: Emit per-reviewer arms from the paired acceptance report

PRD promises: P1, P3, P4, P6.

Public boundary: run_paired_acceptance_pilot returns and persists paired_acceptance_report.json for a configured full-panel fixture run.

Acceptance criteria:
- The report contains S_probe, conservative S_full, and a top-level per_reviewer_arms object.
- Each reviewer arm contains per-candidate decisions, FAR, TAR, denominators, availability counts, and evidence metadata.
- Inter-reviewer agreement is reported separately from the aggregate S_full arm.
- The panel marginal is computed only when matched true-accept permits it, otherwise a typed status is returned.

## Issue 2: Prove full-panel availability, isolation, and report-only behavior

PRD promises: P1, P2, P5, P6.

Public boundary: fixture calibration tests and the produced report artifact demonstrate full-roster configured reviewer evidence.

Acceptance criteria:
- Both configured reviewers can populate full-roster rows when available.
- Missing reviewer verdicts make the affected full-gate rows unavailable.
- Cursor worktree isolation does not surface cursor_modified_worktree from host writes.
- Oracle-isolation and hidden-field leak checks pass for the produced report.
- Report-only flags remain false for policy mutation and improvement claims.
