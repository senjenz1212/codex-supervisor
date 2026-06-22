# TDD Plan

## Public-Boundary RED 1: Diagnostic Corpus Growth Report

### test_fixture_diagnostic_corpus_growth_reports_slice1a_positive_denominator

Maps to: Slice 1, P1, P2, P4, P5.

RED: Write a public-boundary test through the fixture measurement runner/report interface that expects the diagnostic corpus report to include a Slice 1A growth rationale, a larger oracle-positive denominator than the 1A value of three, retained negative denominator coverage, report-only policy flags, and no applyable improvement claim.

GREEN: Add the minimal diagnostic fixture coverage and report fields needed for the runner to export that evidence.

## RED 2: Hidden Oracle Isolation For New Fixtures

### test_fixture_diagnostic_corpus_growth_excludes_hidden_oracle_material

Maps to: Slice 1, P3.

RED: Build reviewer packets or public worktrees for the grown corpus and assert no hidden oracle paths, final_score, oracle_accept, expected outcomes, protected artifacts, or hidden test commands appear in public evidence.

GREEN: Reuse the existing oracle leak detector and fixture public-worktree construction for every new diagnostic candidate.

## RED 3: Controls And False-Accept Traps Remain Present

### test_fixture_diagnostic_corpus_growth_preserves_controls_and_false_accept_traps

Maps to: Slice 1, P2.

RED: Validate the grown corpus manifest and fail when it lacks a positive control, a negative control, or a public-pass hidden-fail false-accept trap.

GREEN: Add or preserve manifest metadata so the grown diagnostic corpus retains those control classes.

## RED 4: TAR Loss And Confidence Intervals Stay Visible

### test_fixture_diagnostic_report_exports_tar_loss_and_confidence_intervals

Maps to: Slice 2, P4.

RED: Inspect the exported paired acceptance report and require n_good, n_bad, S_probe false accepts, S_full false accepts, true-accept loss, matched-TAR status, and confidence intervals.

GREEN: Extend report assembly only enough to export the required diagnostic fields.

## RED 5: Calibration Cannot Become Improvement

### test_fixture_diagnostic_report_stays_calibration_only

Maps to: Slice 2, P5.

RED: Fail if a grown fixture report sets metric_applyable, improvement_claim_allowed, policy_mutated, gate_advanced, or best-of-K held-out improvement language from calibration-only evidence.

GREEN: Preserve the report-only flags and recommendation wording when the diagnostic report is exported.

## Refactor Notes

Keep the measurement runner as the interface under test. Helper extraction is allowed for corpus rationale, manifest validation, or report assembly only after the public-boundary tests are green.
