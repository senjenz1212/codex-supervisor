# TDD Plan

Mode: one RED, minimal GREEN, repeat.

## First RED

Test: `tests/test_cross_family_panel.py::test_same_family_panel_blocks_independence_measurement`

Public boundary: `_build_official_all_arms_diagnostic_report`

Expected failure before implementation: no `reviewer_roster_cross_family_verification` field and `all_arms_populated` stays true when all arms execute.

Minimal GREEN:

- Add official roster verification over reviewer analysis rows.
- Append `reviewer_roster_not_verified_cross_family` to blocked reasons when verification fails.
- Make `all_arms_populated` require both execution availability and roster verification.

## Next RED

Test: `tests/test_cross_family_panel.py::test_litellm_reviewer_reports_real_provider_family`

Public boundary: `ReviewerAdapter.review` plus `independent_reviewer_results_from_review_results`

Minimal GREEN:

- Add `LiteLLMReviewer`.
- Thread explicit LiteLLM model/provider knobs.
- Preserve `ReviewerSpec` provenance in result rows.

## Next RED

Test: `tests/test_cross_family_panel.py::test_all_arms_unavailable_when_roster_unverified_cross_family`

Public boundary: `_build_official_all_arms_diagnostic_report`

Minimal GREEN:

- Treat `openai_compatible`, `unknown`, mixed family, and Cursor default as unproven.
- Return unavailable independence metrics when verification fails.

## Regression Loop

- `tests/test_allarms_diversity.py` keeps verified diversity metrics computed and updates the no-reviewer blocked shape.
- SWE-bench all-arms bridge tests prove existing completed and unavailable reports still preserve report-only authority.
