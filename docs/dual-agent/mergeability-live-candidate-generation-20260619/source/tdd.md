# TDD Plan

## Public Boundary

The first RED tests exercise `run_live_mergeability_candidate_generation`, a public mergeability bench report interface. Fake generator adapters stand in for external model calls, while mergeability task loading, public input construction, oracle isolation, candidate hashing, held-out oracle grading, and policy derivation use real code paths.

## RED-GREEN Cycles

### test_live_generation_requires_allow_live_before_generators_run

Maps to: Slice 1, P1, P7

Red: Call the live report interface with `allow_live=false` and fake baseline and supervisor generators that fail the test if invoked. Assert the report status is unavailable, the reason names disabled live mode, no candidate artifacts exist, and report-only invariants remain false.

Green: Add the smallest live report wrapper that checks `allow_live` before generator invocation and returns the non-applyable unavailable report shape.

### test_live_generation_requires_budget_matched_arms

Maps to: Slice 1, P2, P6

Red: Call the live report interface with mismatched model, provider, budget, or timeout metadata between arms. Assert generation is unavailable, the mismatch reason is recorded, and no arm is accepted.

Green: Add arm metadata validation and canonical recording before execution.

### test_live_generation_excludes_hidden_oracle_material_from_generator_inputs

Maps to: Slice 2, P3

Red: Use a fake generator that records its received prompt, public worktree manifest, visible commands, and metadata. Assert hidden test commands, hidden fixture paths, final scores, expected outcomes, oracle labels, and protected path content are absent.

Green: Reuse the existing public fixture copy and leak detector to construct generator inputs from the public task surface only.

### test_live_generation_records_stable_candidate_artifact_hashes

Maps to: Slice 2, P4

Red: Return deterministic candidate files from both fake generator adapters and assert each arm records a stable candidate artifact hash, prompt hash, evaluator hash, wall-clock, cost, and token usage fields.

Green: Canonicalize generated candidate payloads and hash them with stable JSON serialization.

### test_live_generation_evaluates_both_arms_with_same_heldout_oracle

Maps to: Slice 2, P2, P3, P7

Red: Generate one known-good and one known-bad candidate for the same held-out task. Assert both arms are graded through the same bench oracle, use the same evaluator hash, and retain baseline versus supervisor decisions without leaking hidden material into generator or reviewer inputs.

Green: Convert generated artifacts to `MergeabilityCandidate` values and grade them with `grade_mergeability_candidate` after generation.

### test_candidate_affects_evaluated_path_false_for_non_evaluated_change

Maps to: Slice 3, P5

Red: Run the evaluator quality surface with a candidate that changes only documentation or another non-evaluated path. Assert the manifest records `candidate_affects_evaluated_path=false` and does not claim a meaningful behavior delta.

Green: Add evaluated-path derivation using changed paths and allowed evaluated surfaces rather than a constant.

### test_candidate_affects_evaluated_path_true_for_evaluated_delta

Maps to: Slice 3, P5

Red: Run the evaluator quality surface with a candidate that changes an evaluated mutable path or has a positive evaluated-path control delta. Assert the manifest records `candidate_affects_evaluated_path=true`.

Green: Extend the derivation to recognize evaluated mutable paths and explicit control deltas.

### test_live_generation_budget_overrun_is_unavailable_not_accepted

Maps to: Slice 1, Slice 4, P6, P7

Red: Make one fake generator report cost or wall-clock over budget. Assert the corresponding arm is rejected or unavailable, the overrun flag is recorded, and the report cannot be used as an accepted improvement.

Green: Add budget and timeout enforcement at the live generation arm boundary.

### test_live_generation_report_cannot_create_policy_proposal

Maps to: Slice 4, P7

Red: Pass a successful live calibration report into policy proposal derivation. Assert no proposal is produced, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

Green: Preserve the existing non-applyable policy boundary for live generation reports.

## Translation Audit

Every PRD promise has at least one public-boundary test name. The first RED test starts at the live mergeability report interface rather than a helper. The external generator is the only mocked seam because model invocation is outside the supervisor process. Hidden oracle exclusion, candidate hashing, budget overrun, evaluated-path derivation, and policy non-applyability all have explicit forbidden outcomes in the tests.
