# TDD Plan

Task id: panel-verified-crossfamily-robust-20260626

Mode: one RED, one minimal GREEN, repeat.

## First RED

Test: `tests/test_panel_verified_crossfamily.py::test_provider_family_from_served_model_not_flag`

Public boundary: `independent_reviewer_results_from_review_results` plus `_reviewer_cross_family_claim_status`.

Expected failure before implementation: the LiteLLM operator/provider config can make a result appear as `google` even when the served model returned a different or unproven family.

Minimal GREEN:

- Derive verified provider family from `CursorInvocationResult.model`.
- Mark served-model-derived concrete families as verified.
- Prevent `ReviewerSpec.provider_family` from upgrading a LiteLLM result to proven family evidence.

## Next RED

Test: `tests/test_panel_verified_crossfamily.py::test_operator_flag_alone_is_not_proven`

Public boundary: `_reviewer_cross_family_claim_status`.

Expected failure before implementation: a row with `provider_family="google"` and no served-model verification counts as proven.

Minimal GREEN:

- Require `provider_family_verified=True` or an accepted non-operator `provider_family_source`.
- Return an unproven status for operator-only provider family claims.

## Next RED

Test: `tests/test_panel_verified_crossfamily.py::test_robust_aggregation_bounds_one_contaminated_judge`

Public boundary: `evaluate_reviewer_panel`.

Expected failure before implementation: `aggregation_mode="geometric_median"` is not accepted and a single contaminated deny forces conservative revise behavior.

Minimal GREEN:

- Add geometric-median aggregation mode.
- Compute robust location over reviewer acceptance scores.
- Report robust diagnostics in the panel decision.

## Next RED

Test: `tests/test_panel_verified_crossfamily.py::test_unverified_panel_blocks_measurement`

Public boundary: `_build_official_all_arms_diagnostic_report`.

Expected failure before implementation: a proven-family but conservatively aggregated panel can still satisfy cross-family readiness because aggregation mode is not part of roster verification.

Minimal GREEN:

- Carry panel aggregation mode into official reviewer analysis rows.
- Block roster verification when any available panel row is not robustly aggregated.
- Append `reviewer_panel_not_robustly_aggregated` to unavailable reasons and keep independence metrics unavailable.

## Regression Loop

- Run `tests/test_panel_verified_crossfamily.py`.
- Run `tests/test_cross_family_panel.py`.
- Run `tests/test_reviewer_panel_aggregation.py`.
- Run focused all-arms bridge tests if report shapes changed.
