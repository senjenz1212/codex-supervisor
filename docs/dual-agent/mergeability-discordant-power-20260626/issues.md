# Tracer Bullet Issues

## Issue 1: Add paired-power report block

PRD promise: P1

Public boundary for first RED test: `run_powered_factorial_mergeability_evaluation`

Chosen seam or interface for first RED test: report `paired_power`

Tracer bullet: Emit a `paired_power` block for the full-supervisor-stack comparison using existing paired discordant counts.

Allowed outcomes preserved from PRD: paired power is report-visible; all authority flags remain false.

Forbidden outcomes preserved from PRD: no policy bridge, no raw count-only powered result.

TDD plan: RED `test_all_concordant_rows_report_underpowered`; GREEN add `_paired_power_sufficiency` and report block.

## Issue 2: Block applyability on all-concordant rows

PRD promise: P1

Public boundary for first RED test: `run_powered_factorial_mergeability_evaluation`

Chosen seam or interface for first RED test: top-level `metric_applyable`

Tracer bullet: Show that raw denominator sufficiency can be true while paired power blocks `metric_applyable`.

Allowed outcomes preserved from PRD: `metric_applyable` false when discordant pairs are zero.

Forbidden outcomes preserved from PRD: declaring sufficient with all-concordant rows.

TDD plan: RED at report boundary; GREEN require paired-power status for `metric_applyable`.

## Issue 3: Use exact binomial for sparse discordance

PRD promise: P1

Public boundary for first RED test: `run_powered_factorial_mergeability_evaluation`

Chosen seam or interface for first RED test: `paired_power.comparisons.full_supervisor_stack.method`

Tracer bullet: For discordant pairs below the asymptotic threshold, report exact-binomial method and p-value.

Allowed outcomes preserved from PRD: exact method under threshold.

Forbidden outcomes preserved from PRD: asymptotic chi-square on sparse discordance.

TDD plan: RED `test_exact_binomial_used_under_25_discordant`; assert raw sample size is sufficient, discordance is nonzero but below threshold, `paired_power.status` is underpowered, and `metric_applyable` remains false. GREEN implement exact two-sided binomial.

## Issue 4: Use continuity-corrected McNemar above threshold

PRD promise: P1

Public boundary for first RED test: `run_powered_factorial_mergeability_evaluation`

Chosen seam or interface for first RED test: `paired_power.comparisons.*.method`

Tracer bullet: Once discordant pairs meet the threshold, report continuity-corrected McNemar chi-square.

Allowed outcomes preserved from PRD: method and statistic are visible.

Forbidden outcomes preserved from PRD: uncorrected chi-square or no paired test.

TDD plan: RED `test_continuity_corrected_mcnemar_used_at_threshold`; GREEN add continuity-corrected chi-square method, statistic, p-value, alpha, and pass criterion.

## Issue 5: Keep raw sample size descriptive

PRD promise: P1

Public boundary for first RED test: `run_powered_factorial_mergeability_evaluation`

Chosen seam or interface for first RED test: `sample_size_sufficiency` plus `paired_power`

Tracer bullet: Preserve raw `n_bad`/`n_good` fields while making them non-authoritative, and split guardrails into raw sample-size and paired-power threshold signals.

Allowed outcomes preserved from PRD: denominator evidence remains visible.

Forbidden outcomes preserved from PRD: raw counts alone satisfying powered.

TDD plan: Assert raw sample size can be sufficient while paired power is underpowered and `promotion_guardrails.powered_threshold_met` remains false.

## Issue 6: Add compact rate interval objects

PRD promise: P2

Public boundary for first RED test: powered factorial arm summaries

Chosen seam or interface for first RED test: `_summarize_acceptance_arm`

Tracer bullet: Add rate objects with `rate`, `ci_low`, `ci_high`, and interval provenance for FAR/TAR/FRR while preserving existing keys.

Allowed outcomes preserved from PRD: every rate carries interval bounds.

Forbidden outcomes preserved from PRD: bare point estimates only.

TDD plan: RED `test_rates_carry_wilson_intervals`; assert FAR/TAR/FRR compact rate objects, confidence interval objects, and non-Wald method labels. GREEN add shared interval summary fields.

## Issue 7: Apply rule-of-three to zero-event arms

PRD promise: P2

Public boundary for first RED test: powered factorial arm summaries

Chosen seam or interface for first RED test: `_wilson_interval`

Tracer bullet: FAR/TAR/FRR `0/n` reports lower `0.0` and upper near `3/n`.

Allowed outcomes preserved from PRD: zero-event upper bound communicates residual risk.

Forbidden outcomes preserved from PRD: bare zero or Wald interval.

TDD plan: RED `test_far_zero_reports_rule_of_three_upper_bound`; assert zero-event FAR, TAR, and FRR use the same rule-of-three branch. GREEN add zero-event branch.

## Issue 8: Project FRR interval into `far_tar_frr`

PRD promise: P2

Public boundary for first RED test: SWE-bench `far_tar_frr`

Chosen seam or interface for first RED test: reused arm summaries from `_summarize_acceptance_arm`

Tracer bullet: Include FRR confidence interval wherever `far_tar_frr` exposes FRR.

Allowed outcomes preserved from PRD: FAR/TAR/FRR interval parity.

Forbidden outcomes preserved from PRD: missing FRR interval field.

TDD plan: Extend interval coverage assertions to require `report["far_tar_frr"][arm]["false_reject_confidence_interval"]` for every projected arm.

## Issue 9: Preserve false authority flags

PRD promise: P1, P2

Public boundary for first RED test: report authority fields

Chosen seam or interface for first RED test: report assembly guardrails

Tracer bullet: The paired gate may affect `metric_applyable`, but no policy authority flag flips.

Allowed outcomes preserved from PRD: `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` remain false.

Forbidden outcomes preserved from PRD: autonomous policy mutation or proposal creation.

TDD plan: Assert existing authority fields remain false, `promotion_guardrails.policy_mutation_allowed is False`, `recommendation.applyable_policy_proposal is False`, and policy derivation returns no proposals.

## Issue 10: Export replayable paired-power evidence

PRD promise: P1

Public boundary for first RED test: exported powered factorial report JSON

Chosen seam or interface for first RED test: report serialization path

Tracer bullet: `paired_power` survives export and is included in `report_sha256`.

Allowed outcomes preserved from PRD: replayable report includes paired test evidence.

Forbidden outcomes preserved from PRD: in-memory-only paired evidence.

TDD plan: Existing export test is extended if needed after report-block tests are green.

## Issue 11: Run translation and validation gate

PRD promise: P1, P2

Public boundary for first RED test: packet translation audit plus no-mistakes validation

Chosen seam or interface for first RED test: test list and committed source/test diff

Tracer bullet: Bind the PRD promises to tests, implementation, commit, and no-mistakes gate.

Allowed outcomes preserved from PRD: code-only, no Docker or live calls.

Forbidden outcomes preserved from PRD: helper-only first RED, silent mocking above the public boundary, policy bridge work.

TDD plan: Run the named tests, relevant mergeability regression tests, commit source plus tests, then run no-mistakes.
