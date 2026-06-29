# Mergeability All-Arms Diversity Wiring TDD Plan

Runtime command for the first cycle:

`pytest tests/test_allarms_diversity.py::test_all_arms_report_includes_independence_metrics`

## Cycle 1

Maps to: P1 and Tracer Bullet 1.

RED: Add `tests/test_allarms_diversity.py::test_all_arms_report_includes_independence_metrics`. The test calls `_build_official_all_arms_diagnostic_report` with a two-candidate, two-reviewer official report fixture. It asserts `inter_reviewer_agreement`, `leave_one_reviewer_out`, and `effective_vote_estimate` exist and are populated, and that each metric is keyed to the same candidate-pool hash. In the same test, call the boundary with no reviewer rows and with zero oracle-grounded reviewer errors, then assert the honest unavailable paths remain unavailable.

GREEN: In `supervisor/swe_bench_mergeability.py`, import the existing metric helpers from `supervisor.mergeability_bench`, derive reviewer panel results from `supervisor_full_gate_reviewer_results`, compute per-reviewer arms and pairwise oracle error overlap with existing helpers, call the three requested metric functions, and attach their results beside reviewer provenance and generator disjointness.

Refactor: Keep any adapter input derivation local and small. Do not duplicate the metric algorithms, add new authority, or widen the public report contract beyond the requested diagnostic fields and shared candidate-pool hash.

## Boundary Rules

The first RED is at the public boundary named in P1. Fixtures may construct official-report-shaped dictionaries below that boundary; tests must not mock `_inter_reviewer_agreement`, `_leave_one_reviewer_out_analysis`, `_effective_vote_estimate`, or the report assembly under test.

## Regression Sweep

After the first cycle is green, run:

`pytest tests/test_allarms_diversity.py tests/test_swe_bench_pro_mergeability_bridge.py -k 'all_arms or diversity or rubric or official_all_arms'`

Then run a focused full file if the selector is too narrow or misses touched behavior.
