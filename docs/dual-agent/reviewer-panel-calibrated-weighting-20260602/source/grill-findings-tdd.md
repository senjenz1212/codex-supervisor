# TDD Grill Findings

Task id: `reviewer-panel-calibrated-weighting-20260602`

### Finding 1: helper-only calibration tests would miss the workflow behavior

Status: resolved

Evidence: a calibration builder can pass while the workflow still ignores the
artifact.

Resolution: the TDD plan includes public-boundary workflow tests for correlated
accept escalation, independent accept advance, missing-artifact fallback, and
real revise blocking.

### Finding 2: deterministic calibration needs contrasting fixtures

Status: resolved

Evidence: asserting a nonempty weight map does not prove weights are measured.

Resolution: the deterministic test compares an effectively independent fixture
against a correlated fixture and asserts different weights and different
calibration hashes.

### Finding 3: fallback must be explicit in tests

Status: resolved

Evidence: optional artifact paths can accidentally fail closed or apply hidden
defaults.

Resolution: `test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative`
asserts inactive route metadata, conservative aggregation mode, and no
calibration payload.

### Finding 4: severity weighting needs a direct regression

Status: resolved

Evidence: correlated and independent fixtures prove dependency weighting, but a
regression could remove severity multipliers without failing those tests.

Resolution: the TDD plan includes
`test_evaluate_reviewer_panel_calibrated_accept_applies_severity_multiplier`,
which asserts the calibrated accept payload includes severity multipliers and
uses them in aggregate confidence.

### Finding 5: calibration activation must reject no-lineage constants

Status: resolved

Evidence: a schema-valid blob with only `reviewer_weights` can be hand-authored
and would violate the "measured, not constants" promise if it became active.

Resolution: the TDD plan includes
`test_load_reviewer_panel_calibration_rejects_no_lineage_constants`, and the
loader requires source report hash, labeled-set hash, pairwise dependency,
per-reviewer derived-from-pairwise lineage, and a matching calibration digest.
