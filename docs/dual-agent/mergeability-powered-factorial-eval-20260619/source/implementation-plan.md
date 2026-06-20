# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `supervisor/autoresearch/policy_evolution.py`
- `tests/test_mergeability_bench.py`
- `tests/test_autoresearch_policy_evolution.py`
- `docs/dual-agent/mergeability-powered-factorial-eval-20260619/source/tdd.md`

## Risks

- Matched true-accept can be misreported if candidate-pool equality is checked after metrics are summarized.
- Oracle ceiling can accidentally look like a normal arm if arm-role metadata is not enforced consistently.
- Reviewer marginal analysis can overclaim correlation when the fixture has too few paired reviewer decisions.
- A powered-threshold-met metric can be mistaken for permission to mutate policy unless report-only invariants are tested directly.

## Traceability

- P1 -> test_powered_factorial_report_includes_all_labeled_arms
- P2 -> test_powered_factorial_uses_same_candidate_pool_across_arms
- P3 -> test_matched_tar_refuses_unmatched_comparisons
- P3 -> test_powered_factorial_records_far_tar_frr_confidence_and_discordance
- P4 -> test_oracle_ceiling_cannot_be_reported_as_supervisor_improvement
- P5 -> test_leave_one_reviewer_out_records_marginal_effects_and_correlation
- P5 -> test_reviewer_unavailable_blocks_full_stack_claim
- P6 -> test_reviewer_unavailable_blocks_full_stack_claim
- P6 -> test_gaming_flagged_factorial_run_creates_no_applyable_proposal
- P6 -> test_powered_threshold_unmet_keeps_metric_non_applyable
- P7 -> test_powered_threshold_met_may_allow_metric_but_never_mutates_policy
- P8 -> test_powered_factorial_exports_replayable_artifacts_and_trend_row

## Steps

1. Add the public powered-factorial report function to the mergeability bench module, accepting deterministic candidate rows, arm decisions, reviewer rows, powered-threshold settings, output directory, and trend metadata.
2. Reuse existing arm summary and confidence interval helpers where possible, but add factorial-specific arm labels, same-pool validation, paired discordant counts, sample-size sufficiency, and matched-TAR refusal.
3. Add reviewer-level leave-one-reviewer-out analysis that reports unavailable status when reviewer evidence is missing, and reviewer marginal effects when enough public decisions exist.
4. Add promotion guardrails that keep oracle ceiling non-promotable, gaming-flagged reports non-applyable, reviewer-unavailable full-stack claims unavailable, and policy mutation disabled even for powered evidence.
5. Export replayable report artifacts and observational trend rows without writing ledger events from the pure report function.
6. Implement the TDD tests in vertical slices, running the named mergeability and policy-evolution tests after each meaningful cycle.
