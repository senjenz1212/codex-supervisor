# TDD Grill Findings: Proposal Completion Audit

### Finding 1: The First Proof Must Exercise The Workflow Boundary

status: resolved

Concern: A helper-only check could validate markdown shape while missing whether the actual supervisor workflow accepts or blocks the audit.

Resolution: `test_planning_artifacts_pass_all_route_gates` covers the deterministic planning validator locally, but `test_terminal_gate_status_is_reported_truthfully` requires the durable workflow boundary and terminal gate evidence.

### Finding 2: The Audit Needs Negative Assertions Against Mutation

status: resolved

Concern: The TDD plan initially focused on report content and could miss accidental activation, approval, apply, or overlay mutation.

Resolution: `test_report_only_audit_does_not_mutate_policy_or_queue` requires pre-run and post-run state comparison for the overlay and queue/proposal state.

### Finding 3: Liveness Claims Need Denominator Discipline

status: resolved

Concern: The trend output can contain numbers that look decision-ready but are not normalized for the decision being made.

Resolution: `test_liveness_metrics_are_not_overclaimed` requires the report to identify whether the denominator is per run, per poll, or merely incident share before using the metric for D1 or D2.

### Finding 4: Cursor And Claude Gate Verdicts Remain Authoritative

status: resolved

Concern: The report could be useful but still fail the reviewer or planning gates.

Resolution: The exit criteria require terminal workflow status and reviewer decisions. Usefulness alone is not treated as gate acceptance.
