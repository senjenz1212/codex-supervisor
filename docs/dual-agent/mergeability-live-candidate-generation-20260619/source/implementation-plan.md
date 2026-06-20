# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py` for the public live report interface, generator input construction, candidate artifact hashing, budget enforcement, and held-out oracle grading.
- `supervisor/autoresearch/evaluator.py` for replacing the constant evaluated-path quality flag with derived behavior.
- `tests/test_mergeability_bench.py` for public-boundary report tests, generator oracle-isolation tests, stable hash assertions, and report-only policy checks.
- `tests/test_autoresearch_evaluator_quality.py` or the existing mergeability test module for evaluator quality manifest true and false cases, depending on the existing local test organization.

## Risks

- Live generation introduces an external spending seam, so the adapter must be injectable and disabled by default or normal tests could accidentally invoke providers.
- Oracle isolation can regress through prompts, worktrees, reviewer packets, or receipts, so the implementation must reuse the existing leak detector and inspect captured generator input in tests.
- Budget matching can be overclaimed if checked after generation, so arm metadata should be validated before invoking either adapter.
- Evaluated-path derivation can become too broad if it treats every changed file as behavior-changing, so false cases must include non-evaluated changes.

## Traceability

P1 maps to `test_live_generation_requires_allow_live_before_generators_run`.
P2 maps to `test_live_generation_requires_budget_matched_arms` and `test_live_generation_evaluates_both_arms_with_same_heldout_oracle`.
P3 maps to `test_live_generation_excludes_hidden_oracle_material_from_generator_inputs`.
P4 maps to `test_live_generation_records_stable_candidate_artifact_hashes`.
P5 maps to `test_candidate_affects_evaluated_path_false_for_non_evaluated_change` and `test_candidate_affects_evaluated_path_true_for_evaluated_delta`.
P6 maps to `test_live_generation_budget_overrun_is_unavailable_not_accepted`.
P7 maps to `test_live_generation_report_cannot_create_policy_proposal` and the report-only assertions in every live report test.
