# Tri-Agent Findings

Task id: panel-verified-crossfamily-robust-20260626

## Agent A: Family Provenance

Verdict: accepted with caveat.

Key evidence:

- `LiteLLMReviewer` already routes through `litellm_structured`.
- `configured_reviewers` threads `litellm_provider_family` into `ReviewerSpec.provider_family`.
- `_result_with_spec_provenance` is the high-leverage seam because it currently can copy spec family into result rows before mergeability reporting.
- `_reviewer_cross_family_claim_status` is the measurement proof gate.

Folded change:

- P1 and tests now require `provider_family_verified=True` and `provider_family_source="served_model"` before a family counts as proven. Operator config is descriptive only.

## Agent B: Robust Aggregation

Verdict: revise.

Key evidence:

- `evaluate_reviewer_panel` currently emits only `conservative` or `calibrated_weighted`.
- Configured mergeability panels consume `evaluate_reviewer_panel` and pass the nested panel decision into S_full.
- Hard blocks and missing verdicts must remain fail-closed.

Folded change:

- The robust aggregation test uses a low-severity contaminated non-accepting reviewer, so geometric median can bound contamination without weakening existing critical/important hard blocks.
- The TDD plan keeps `evaluate_reviewer_panel` as the public aggregation seam and preserves conservative default behavior.

## Agent C: Measurement Gate

Verdict: revise.

Key evidence:

- Existing all-arms readiness blocks on cross-family verification, but `_official_reviewer_analysis_rows` drops `panel_decision`.
- Missing data path: `official_report.replay_report.instance_reports[].independent_reviewer_results[].panel_decision.aggregation_mode -> _official_reviewer_analysis_rows -> _official_reviewer_roster_cross_family_verification`.
- Authority flags are already hard false in the official diagnostic report.

Folded change:

- P2, issues, and tests require per-row panel aggregation metadata to reach roster verification.
- Non-robust aggregation must add `reviewer_panel_not_robustly_aggregated`, block `all_arms_populated`, and keep independence metrics unavailable.
