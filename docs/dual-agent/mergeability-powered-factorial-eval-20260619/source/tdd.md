# TDD Plan

## Public Boundary

The first RED tests exercise a public mergeability factorial report interface. The test fixture supplies deterministic candidate rows, arm decisions, reviewer-level decisions, gaming flags, and powered-threshold settings. External generation, live reviewer calls, and ledger mutation remain below or outside the test seam, while report construction, metric computation, promotion guardrails, oracle-ceiling labeling, and policy-derivation refusal use real code paths.

## RED-GREEN Cycles

### test_powered_factorial_report_includes_all_labeled_arms

Maps to: P1, P8

Red: Build a minimal shared candidate pool and call the factorial report interface. Assert the report contains exactly `single_agent_baseline`, `same_model_multi_agent`, `hetero_multi_reviewer`, `runtime_evidence_floor`, `full_supervisor_stack`, and `oracle_ceiling`, each with independent labels, decision sources, and non-empty replay metadata.

Green: Add the smallest public report function and arm-normalization logic needed to return the required labeled arms.

### test_powered_factorial_uses_same_candidate_pool_across_arms

Maps to: P2

Red: Provide one arm with a missing candidate identifier and assert the report marks the comparison unavailable with a candidate-pool mismatch reason instead of computing deltas.

Green: Add candidate-pool canonicalization and equality validation before any paired comparison is computed.

### test_matched_tar_refuses_unmatched_comparisons

Maps to: P3

Red: Provide shared candidates where the baseline and full supervisor stack have different true-accept rates. Assert matched-TAR status is `not_matched`, FAR improvement is not reported, and the reason names the mismatched true-accept rates.

Green: Reuse or generalize matched true-accept comparison logic so unmatched TAR returns a refusal object.

### test_powered_factorial_records_far_tar_frr_confidence_and_discordance

Maps to: P3

Red: Provide a small paired candidate set with known oracle labels and arm decisions. Assert FAR, TAR, FRR, denominators, Wilson confidence intervals, and paired discordant counts match the fixture.

Green: Add per-arm metric summaries and paired discordant-count computation at the report boundary.

### test_oracle_ceiling_cannot_be_reported_as_supervisor_improvement

Maps to: P4

Red: Attempt to select `oracle_ceiling` as the reported supervisor improvement arm. Assert the report records an oracle-ceiling promotion violation, keeps the metric non-applyable, and preserves the ceiling only as a reference line.

Green: Add oracle-ceiling role validation and anti-promotion guardrails.

### test_leave_one_reviewer_out_records_marginal_effects_and_correlation

Maps to: P5

Red: Provide reviewer-level decisions where one reviewer changes the panel result and another is redundant. Assert leave-one-reviewer-out rows record marginal effect, changed decisions, and reviewer correlation or overlap indicators.

Green: Add reviewer marginal analysis over reviewer decision rows, with unavailable status when evidence is insufficient.

### test_reviewer_unavailable_blocks_full_stack_claim

Maps to: P5, P6

Red: Mark reviewer-panel evidence unavailable for the full supervisor stack and pass the report to policy proposal derivation. Assert the full-stack claim is unavailable, matched comparison is unavailable, no acceptance is imputed from another arm, `metric_applyable=false`, `improvement_claim_allowed=false`, and no applyable policy proposal is produced.

Green: Thread reviewer availability into full-stack arm status, top-level metric applyability, improvement-claim eligibility, and policy-derivation guardrails.

### test_gaming_flagged_factorial_run_creates_no_applyable_proposal

Maps to: P6

Red: Build a factorial report with a gaming flag and pass it to policy proposal derivation. Assert no applyable proposal is created and the report records the blocking gaming flag.

Green: Preserve existing policy-derivation refusal for gaming-flagged and non-applyable reports, adding factorial labels if needed.

### test_powered_threshold_unmet_keeps_metric_non_applyable

Maps to: P6

Red: Provide a valid but underpowered report. Assert sample-size sufficiency is unmet, `metric_applyable=false`, and `improvement_claim_allowed=false`.

Green: Add powered-threshold evaluation over minimum bad and good denominators plus matched-TAR status.

### test_powered_threshold_met_may_allow_metric_but_never_mutates_policy

Maps to: P7

Red: Provide a report satisfying powered threshold, matched TAR, no gaming flags, and available reviewer evidence. Assert `metric_applyable=true` is allowed, while `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false` remain false.

Green: Separate metric applyability from policy mutation and gate authority in the report shape.

### test_powered_factorial_exports_replayable_artifacts_and_trend_row

Maps to: P8

Red: Call the report interface with an output directory and trend metadata. Assert replay artifacts are written, artifact hashes are stable, trend rows are returned, and no ledger write side effects occur.

Green: Add deterministic export and observational trend-row construction.

## Translation Audit

Every PRD promise has at least one public-boundary RED test. The first test starts at the report interface rather than helper math. Candidate-pool equality, matched true-accept refusal, oracle-ceiling anti-promotion, reviewer marginal effects, gaming flags, powered-threshold status, and policy non-mutation all name observable report outcomes and forbidden states.
