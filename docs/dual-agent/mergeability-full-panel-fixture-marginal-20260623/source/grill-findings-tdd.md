# TDD Grill Findings

Status: accepted after tightening the public-boundary proof.

- Resolved: Tests start at run_paired_acceptance_pilot and paired_acceptance_report.json, not private helpers.
- Resolved: The test set pins net-new behavior: full-roster rows, per-reviewer arms, inter-reviewer agreement, Cursor isolation, typed unavailable states, and report-only leak checks.
- Resolved: Helper behavior is covered only through the report boundary, so a passing result proves the surface operators consume.
- Resolved: The implementation must not modify full-gate accept semantics while adding reviewer-level reporting.
