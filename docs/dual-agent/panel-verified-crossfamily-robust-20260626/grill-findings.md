# PRD Grill Findings

Task id: panel-verified-crossfamily-robust-20260626

## Finding 1: Provider family must not be operator asserted

Status: resolved

Evidence: `configured_reviewers` accepts `litellm_provider_family` and writes it into `ReviewerSpec.provider_family`; `_result_with_spec_provenance` can copy that family into a result when the runtime matches and the derived result family is unproven.

Resolution: P1 requires a verified served-model or response source before a family counts as cross-family. Operator config may remain descriptive, but it cannot set `counts_as_proven_cross_family=True`.

## Finding 2: Robust aggregation needs to be visible to the all-arms gate

Status: resolved

Evidence: `_official_reviewer_roster_cross_family_verification` currently consumes reviewer rows, while the aggregation mode is nested in each S_full panel result.

Resolution: P2 requires reviewer analysis rows to carry panel aggregation metadata and the verification gate to block when aggregation is not robust.

## Finding 3: Robust aggregation must not become a policy authority shortcut

Status: resolved

Evidence: the diagnostic report already carries report-only authority flags and this slice is explicitly out of scope for benchmark-to-policy promotion.

Resolution: implementation must keep `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` false.

## Finding 4: Empty or missing panels must remain unavailable

Status: resolved

Evidence: `evaluate_reviewer_panel` historically used `review_not_required` for empty input, while configured panel wrapper handles empty adapters separately.

Resolution: robust aggregation tests must include real available reviewer inputs, and all-arms readiness must still block missing reviewers separately.
