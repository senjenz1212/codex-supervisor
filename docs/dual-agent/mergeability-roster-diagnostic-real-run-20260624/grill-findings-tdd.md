# TDD Grill Findings

## Finding 1: Helper-only tests would miss the policy leak

Status: resolved.

The plan starts at `run_fixture_panel_produced_baseline_measurement`, `run_paired_acceptance_pilot`, and `run_bounded_parallel_panel_corpus`, which are the public report seams consumed by operators and downstream automation.

## Finding 2: The guard must be machine-readable

Status: resolved.

Tests assert booleans and reason codes rather than relying on `validity_notes` prose.

## Finding 3: Real benchmark execution is intentionally out of this slice

Status: waived for this slice.

This slice prepares the evidence chain and prevents fixture overclaiming. Real SWE-bench execution remains a follow-up after the roster guard is in place.
