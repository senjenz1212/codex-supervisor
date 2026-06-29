# Issues: Panel Verified Cross-Family Robustness

Task id: panel-verified-crossfamily-robust-20260626

## Issue 1: Verify LiteLLM Provider Family From Served Model

PRD promise: P1

Public boundary: `independent_reviewer_results_from_review_results` consumed by `_reviewer_cross_family_claim_status`.

Chosen seam: `CursorInvocationResult.model` as served model id.

Allowed outcomes: served `gemini-*` yields verified `google`; served `claude-*` yields verified `anthropic`.

Forbidden outcomes: operator flag alone yields a proven family.

First RED test: `tests/test_panel_verified_crossfamily.py::test_provider_family_from_served_model_not_flag`.

## Issue 2: Preserve Descriptive Operator Config Without Proven Status

PRD promise: P1

Public boundary: configured LiteLLM panel result rows.

Chosen seam: `_result_with_spec_provenance`.

Allowed outcomes: descriptive config can be retained with `provider_family_source="operator_config"` and `provider_family_verified=False`.

Forbidden outcomes: unproven config upgrades `openai_compatible` to proven.

First RED test: `tests/test_panel_verified_crossfamily.py::test_operator_flag_alone_is_not_proven`.

## Issue 3: Record Live LiteLLM Served Model

PRD promise: P1

Public boundary: `invoke_cursor_agent` returning `CursorInvocationResult` for `litellm_structured`.

Chosen seam: `_run_litellm_structured` response metadata.

Allowed outcomes: response model id is retained when the OpenAI-compatible server returns one.

Forbidden outcomes: only requested proxy alias is recorded when the response supplies a more specific served model id.

First RED test: covered by fixture-level result translation; live infra remains faked below the public boundary.

## Issue 4: Add Robust Geometric-Median Aggregation Mode

PRD promise: P2

Public boundary: `evaluate_reviewer_panel`.

Chosen seam: `aggregation_mode="geometric_median"`.

Allowed outcomes: the robust mode reports `aggregation_mode="geometric_median"` and accepts a two-of-three accepting panel despite one contaminated deny.

Forbidden outcomes: conservative or calibrated weighted output is relabeled as robust.

First RED test: `tests/test_panel_verified_crossfamily.py::test_robust_aggregation_bounds_one_contaminated_judge`.

## Issue 5: Thread Robust Aggregation Through Configured Panel

PRD promise: P2

Public boundary: `build_configured_reviewer_panel`.

Chosen seam: `ConfiguredReviewerPanelOptions.panel_aggregation_mode`.

Allowed outcomes: configured panel callers can opt into geometric-median aggregation.

Forbidden outcomes: S_full configured panel silently uses conservative aggregation when robust readiness is claimed.

First RED test: covered by the measurement gate fixture using panel decision metadata.

## Issue 6: Carry Panel Aggregation Into Official Analysis Rows

PRD promise: P2

Public boundary: `_official_reviewer_analysis_rows`.

Chosen seam: per-candidate `panel_aggregation_mode` copied from the official replay panel result.

Allowed outcomes: roster verification can inspect aggregation mode for every S_full row.

Forbidden outcomes: verification sees only reviewer rows and cannot distinguish robust from naive panels.

First RED test: `tests/test_panel_verified_crossfamily.py::test_unverified_panel_blocks_measurement`.

## Issue 7: Gate All-Arms Readiness On Robust Aggregation

PRD promise: P2

Public boundary: `_build_official_all_arms_diagnostic_report`.

Chosen seam: `_official_reviewer_roster_cross_family_verification`.

Allowed outcomes: unverified or non-robust panels append `reviewer_panel_not_robustly_aggregated` and block readiness.

Forbidden outcomes: `all_arms_populated=True` for conservative, calibrated, missing, or unreported aggregation.

First RED test: `tests/test_panel_verified_crossfamily.py::test_unverified_panel_blocks_measurement`.

## Issue 8: Keep Independence Metrics Unavailable When Gate Fails

PRD promise: P2

Public boundary: `inter_reviewer_agreement`, `leave_one_reviewer_out`, and `effective_vote_estimate` in the official diagnostic.

Chosen seam: `_official_reviewer_independence_metrics(..., roster_verified=False)`.

Allowed outcomes: metrics return unavailable with the same gate reason.

Forbidden outcomes: computed independence metrics on an unverified or non-robust panel.

First RED test: `tests/test_panel_verified_crossfamily.py::test_unverified_panel_blocks_measurement`.

## Issue 9: Preserve Existing Cross-Family Blocks

PRD promise: P1 and P2

Public boundary: existing cross-family panel tests.

Chosen seam: `_reviewer_cross_family_claim_status`.

Allowed outcomes: Cursor default, `openai_compatible`, mixed family, and same-family baseline conflict still block.

Forbidden outcomes: robust aggregation bypasses provenance or generator-disjointness requirements.

First RED test: existing `tests/test_cross_family_panel.py` regressions.

## Issue 10: Keep Report-Only Authority Flags False

PRD promise: P1 and P2

Public boundary: official all-arms diagnostic report.

Chosen seam: `_aeb0_authority_flags`.

Allowed outcomes: all authority flags remain false.

Forbidden outcomes: robust/cross-family readiness mutates policy or advances a gate.

First RED test: existing all-arms report assertions plus new gate fixture.

## Issue 11: Validate Translation Coverage

PRD promise: P1 and P2

Public boundary: packet ledger and TDD plan.

Chosen seam: translation audit.

Allowed outcomes: every PRD promise maps to at least one public-boundary RED test.

Forbidden outcomes: helper-only tests or unreviewed implementation before accepted PRD/issues/TDD ledger entries.

First RED test: human-readable audit in `translation-audit.md`.
