# TDD Plan

## Public Boundary First

The first seam is run_paired_acceptance_pilot because it is the caller-visible fixture calibration interface and the writer of paired_acceptance_report.json. Tests must assert report behavior at that boundary before any helper behavior is trusted.

## Red Green Slices

1. RED: test_configured_full_panel_run_populates_full_roster_rows asserts both configured reviewers create available S_full rows and partial roster evidence stays unavailable. GREEN: preserve configured-panel routing and full-roster accounting.
2. RED: test_full_panel_report_emits_per_reviewer_arms asserts per_reviewer_arms and inter_reviewer_agreement exist with reviewer-level FAR and TAR. GREEN: derive reviewer summaries from supervisor_full_gate_reviewer_results without changing S_full decisions.
3. RED: test_isolated_cursor_reviewer_does_not_flag_modified_worktree asserts Cursor isolation diagnostics remain clean despite host writes. GREEN: preserve isolated worktree diagnostic propagation into report rows.
4. RED: test_panel_marginal_computed_or_honest_not_matched asserts computed status only when true-accept rates match and typed not_matched otherwise. GREEN: keep the existing matched-TAR helper as the metric authority.
5. RED: test_reviewer_infra_failure_marks_unavailable_not_quality_reject asserts infrastructure failure becomes unavailable and panel_missing_verdict_block. GREEN: preserve availability versus quality separation.
6. RED: test_full_panel_report_is_report_only_and_oracle_isolated asserts report-only invariants and oracle leak checks pass. GREEN: add top-level oracle_isolation and hidden_field_leak_check summaries from reviewer packets.

## Verification Commands

Run the slice tests:

```sh
.venv/bin/python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_run_populates_full_roster_rows tests/test_mergeability_bench.py::test_full_panel_report_emits_per_reviewer_arms tests/test_mergeability_bench.py::test_isolated_cursor_reviewer_does_not_flag_modified_worktree tests/test_mergeability_bench.py::test_panel_marginal_computed_or_honest_not_matched tests/test_mergeability_bench.py::test_reviewer_infra_failure_marks_unavailable_not_quality_reject tests/test_mergeability_bench.py::test_full_panel_report_is_report_only_and_oracle_isolated -q
```

Run focused regression coverage:

```sh
.venv/bin/python -m pytest tests/test_mergeability_bench.py tests/test_cursor_agent.py tests/test_dual_agent_slice0.py tests/test_reviewer_panel_aggregation.py -q
```

Run the configured panel fixture measurement and validate the produced paired_acceptance_report.json before commit.
