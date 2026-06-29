# PRD: Mergeability Cross-Family Panel

Task id: mergeability-cross-family-panel-20260626

## Problem Statement

The official all-arms mergeability report can surface reviewer provenance and generator-disjointness diagnostics, but it previously allowed readiness to be computed from arm availability alone. That misframes S_full as a real independent measurement even when the reviewer roster is unproven, same-family with the baseline producer, or collapsed through an OpenAI-compatible proxy label.

## Solution

Add a LiteLLM-backed reviewer adapter that can carry explicit non-OpenAI provider provenance, and promote reviewer roster verification into the official all-arms measurement gate. Arm execution availability remains visible, but `all_arms_populated` and independence metrics stay unavailable unless available reviewers span at least two proven provider families and no proven reviewer family matches the baseline producer family.

Research context:

- PoLL shows that panels of LLM judges can improve reliability when model diversity is real: https://arxiv.org/abs/2404.18796
- METR reward-hacking/self-preference discussion motivates keeping evaluator/generator families disjoint before trusting optimization signals: https://metr.org/blog/2025-06-05-recent-reward-hacking/

## User Stories

1. As a benchmark operator, I want S_full to require proven cross-family reviewers, so that all-arms readiness does not overstate independence.
2. As a benchmark operator, I want same-family reviewer/generator conflicts to block readiness, so that self-preference risk is not hidden behind arm availability.
3. As a reviewer-panel maintainer, I want a LiteLLM reviewer with explicit provider_family provenance, so that non-OpenAI providers can be recorded without treating `openai_compatible` as a proven family.
4. As a future auto-evolve reviewer, I want all authority flags to remain false, so that this diagnostic cannot mutate policy by itself.

## Implementation Decisions

- Keep the existing `ReviewerAdapter` interface and add a LiteLLM adapter behind it.
- Preserve reviewer spec provenance when translating adapter results into independent reviewer panel rows.
- Add explicit LiteLLM model/provider knobs to configured reviewer panel options and CLIs.
- Add an official all-arms roster verification block that is distinct from raw arm execution availability.
- Return blocked independence metrics with reason `reviewer_roster_not_verified_cross_family` when verification fails.

## PRD Promise Contracts

### P1 Promise Contract

Public boundary: `configured_reviewers` and `build_configured_reviewer_panel` through `ReviewerAdapter.review`.

Chosen seam: `ReviewerSpec` plus `independent_reviewer_results_from_review_results`.

Allowed outcomes: A LiteLLM reviewer can run with `reviewer_output_mode="litellm_structured"` and explicit provider_family such as `google` or `anthropic`; the translated result preserves that provider family and marks LiteLLM as text-only.

Forbidden outcomes: `openai_compatible`, `unknown`, or Cursor default model provenance counts as proven cross-family evidence.

### P2 Promise Contract

Public boundary: `_build_official_all_arms_diagnostic_report`.

Chosen seam: `reviewer_roster_cross_family_verification` inside the official diagnostic report.

Allowed outcomes: All execution arms can be available while measurement readiness is blocked; the report records why the reviewer roster is not verified.

Forbidden outcomes: A same-family, unproven, or one-family reviewer roster produces `all_arms_populated=True`, computed independence metrics, or `diagnostic_ready_for_scale=True`.

## Testing Decisions

- First RED test is a public-boundary report test for `_build_official_all_arms_diagnostic_report`.
- Adapter tests fake the external LiteLLM call below the adapter seam; they do not fake the result translation or measurement gate.
- Existing all-arms diversity tests remain as regression coverage for computed metrics when the roster is verified.

## Out of Scope

- Candidate generation.
- Baseline production.
- Reviewer panel powering math.
- Any autonomous benchmark-to-policy bridge or policy mutation.
