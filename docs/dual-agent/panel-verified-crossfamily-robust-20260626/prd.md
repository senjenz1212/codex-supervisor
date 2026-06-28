# PRD: Panel Verified Cross-Family Robustness

Task id: panel-verified-crossfamily-robust-20260626

## Problem Statement

The official S_full panel can currently overstate evaluator independence. A LiteLLM reviewer may be configured with `--litellm-provider-family`, and the result translation can preserve that configured family even when the served model id does not prove the family. The all-arms diagnostic then treats a recorded provider family as proven cross-family evidence. Separately, the panel decision is aggregated by conservative/weighted consensus only; this is not a robust aggregation claim and should not be enough for benchmark readiness when the stated goal is a real benchmark that can later support auto-evolve work.

## Solution

Make provider family evidence verified from the served model/response path instead of from an operator flag alone, and add a geometric-median panel aggregation option that is explicitly reported and required for all-arms readiness. Cross-family verification and robust aggregation remain diagnostic gates only: they may block measurement readiness, independence metrics, and `all_arms_populated`, but they must not mutate policy, advance gates, or authorize improvement claims.

Research context:

- PoLL argues for panel diversity because panels of LLM judges reduce single-model judge bias only when the panel is meaningfully diverse: https://arxiv.org/abs/2404.18796
- ROPOLL motivates a robust aggregation requirement because naive panel consensus can have unbounded bias under contaminated judges, and robust panel aggregation uses a geometric-median style estimator: https://www.amazon.science/publications/ropoll-robust-panel-of-llm-judges

## User Stories

1. As a benchmark operator, I want reviewer family provenance to come from the served model/response, so that `google` or `anthropic` cannot be asserted by a CLI flag alone.
2. As a benchmark operator, I want `openai_compatible`, `unknown`, proxy-default, and operator-asserted families to stay unproven, so that a proxy route cannot masquerade as cross-family evidence.
3. As a benchmark operator, I want S_full to use a robust panel aggregation mode, so that benchmark readiness does not rest on naive consensus.
4. As a benchmark operator, I want the all-arms diagnostic to block independence metrics and `all_arms_populated` unless the roster is both proven-disjoint and robustly aggregated.
5. As an auto-evolve maintainer, I want all authority flags to remain false, so that this slice hardens measurement without building the benchmark-to-policy bridge.

## Implementation Decisions

- Keep `ReviewerAdapter.review` and `ConfiguredReviewerPanelOptions` as the public panel seams.
- Treat `CursorInvocationResult.model` as the served model id. For LiteLLM, update the live OpenAI-compatible call to store `response.model` when available instead of only the requested model.
- Carry `provider_family_verified` and `provider_family_source` through independent reviewer result rows, reviewer summaries, and mergeability normalization.
- Do not allow `_result_with_spec_provenance` to upgrade an unproven LiteLLM result to a proven provider family from `ReviewerSpec.provider_family`.
- Add a geometric-median aggregation mode to the existing `evaluate_reviewer_panel` seam and thread the option through `build_configured_reviewer_panel`.
- Preserve existing conservative and calibrated behavior unless callers opt into robust aggregation.
- Surface panel aggregation mode in official reviewer analysis rows so `_official_reviewer_roster_cross_family_verification` can gate on robust aggregation.
- Keep authority flags false and keep the work out of the autonomous benchmark-to-policy bridge.

## PRD Promise Contracts

### P1 Promise Contract

Public boundary: `_reviewer_cross_family_claim_status` in `supervisor/mergeability_bench.py` and the configured panel roster build in `supervisor/reviewer_registry.py`.

Chosen seam: `LiteLLMReviewer.review` -> `CursorInvocationResult.model` -> `independent_reviewer_results_from_review_results` -> `_normalise_mergeability_reviewer_result`.

Allowed outcomes: A LiteLLM reviewer counts as proven cross-family only when the served model id or response metadata derives a concrete non-OpenAI provider family such as `google` or `anthropic`; the result records `provider_family_verified=True` and a non-operator source.

Forbidden outcomes: `--litellm-provider-family`, `ReviewerSpec.provider_family`, `openai_compatible`, `unknown`, `proxy-default`, or Cursor `default` alone counts as proven cross-family evidence.

### P2 Promise Contract

Public boundary: the panel aggregation used by the S_full accept path in `supervisor/mergeability_bench.py` and the official roster verification in `supervisor/swe_bench_mergeability.py`.

Chosen seam: `evaluate_reviewer_panel(..., aggregation_mode="geometric_median")` and `ConfiguredReviewerPanelOptions.panel_aggregation_mode`.

Allowed outcomes: A robust aggregation option produces a reported `aggregation_mode="geometric_median"` decision, bounds one contaminated judge in the fixture, and is required for all-arms readiness with proven-disjoint families.

Forbidden outcomes: Conservative, calibrated weighted, or naive consensus is reported as bias-bounded or allowed to satisfy all-arms readiness.

## Testing Decisions

- The first RED tests hit public boundaries: configured panel result translation, `evaluate_reviewer_panel`, and `_build_official_all_arms_diagnostic_report`.
- Live model calls are faked below `ReviewerAdapter.review`; tests do not fake result translation, provenance verification, robust aggregation, or all-arms gating.
- Robust aggregation tests use a contamination-shaped fixture, not an assertion that merely echoes a configured mode.
- All authority flags stay false in report fixtures.

## Out of Scope

- SWE-bench Pro oracle execution.
- Candidate corpus generation.
- Produced baseline receipts.
- Powering math and reviewer-panel calibration training.
- Any autonomous benchmark-to-policy bridge, policy mutation, default change, or improvement claim.
