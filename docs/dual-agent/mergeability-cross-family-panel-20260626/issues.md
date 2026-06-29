# Issues: 11 Tracer Bullets

## TB1: Preserve ReviewerSpec Provenance

PRD promise: P1
Public boundary: `independent_reviewer_results_from_review_results`
Tracer bullet: a reviewer result with proxy-looking runtime preserves explicit spec provider_family.
Acceptance: provider_family remains `google`, not `openai_compatible`.

## TB2: Add LiteLLM Reviewer Adapter

PRD promise: P1
Public boundary: `ReviewerAdapter.review`
Tracer bullet: a LiteLLM adapter clones the request to `litellm_structured`.
Acceptance: runner receives the LiteLLM model and output mode.

## TB3: Thread LiteLLM Options Through Configured Panel

PRD promise: P1
Public boundary: `ConfiguredReviewerPanelOptions`
Tracer bullet: configured options can create a LiteLLM reviewer without changing tests that inject reviewers.
Acceptance: optional model/provider knobs flow into `configured_reviewers`.

## TB4: Expose LiteLLM CLI Knobs

PRD promise: P1
Public boundary: SWE-bench mergeability CLI and bounded panel CLI.
Tracer bullet: `--litellm-model` and `--litellm-provider-family` populate configured options.
Acceptance: configured CLI mode can request a non-OpenAI reviewer.

## TB5: Compute Official Roster Verification

PRD promise: P2
Public boundary: `_build_official_all_arms_diagnostic_report`
Tracer bullet: report includes `reviewer_roster_cross_family_verification`.
Acceptance: verification records proven families, unproven reviewers, and baseline conflicts.

## TB6: Block Same-Family Baseline Conflicts

PRD promise: P2
Public boundary: `_build_official_all_arms_diagnostic_report`
Tracer bullet: Google baseline plus Google reviewer plus Anthropic reviewer still blocks.
Acceptance: reason includes `same_family_generator_reviewer_decisive_vote`.

## TB7: Block Unproven Proxy Families

PRD promise: P2
Public boundary: `_build_official_all_arms_diagnostic_report`
Tracer bullet: OpenAI plus `openai_compatible` proxy reviewer blocks.
Acceptance: reason includes `unproven_provider_family`.

## TB8: Separate Execution Availability From Measurement Readiness

PRD promise: P2
Public boundary: official all-arms diagnostic report.
Tracer bullet: all execution arms can be available while readiness is blocked.
Acceptance: `s_full_available=True`, `arm_execution_populated=True`, `all_arms_populated=False`.

## TB9: Block Independence Metrics When Roster Is Unverified

PRD promise: P2
Public boundary: official all-arms diagnostic report.
Tracer bullet: blocked roster returns unavailable independence metrics.
Acceptance: metrics carry `reviewer_roster_not_verified_cross_family`.

## TB10: Keep Verified Diversity Metrics Green

PRD promise: P2
Public boundary: existing all-arms diversity test.
Tracer bullet: verified two-family panels still compute agreement, leave-one-out, and effective vote estimates.
Acceptance: existing diversity metrics remain computed.

## TB11: Preserve Report-Only Authority

PRD promise: P2
Public boundary: diagnostic report authority flags.
Tracer bullet: blocked or completed reports cannot mutate policy.
Acceptance: `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, and `gate_advanced` remain false.
