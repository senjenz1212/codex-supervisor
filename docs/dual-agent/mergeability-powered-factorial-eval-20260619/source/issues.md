# Issue Pack: Powered Factorial Evaluation And Promotion Guardrails

## Slice ISS-1: Add The Factorial Report Boundary

Type: AFK
Priority: P0
Estimate: M
Scope: Create a report-producing interface that accepts deterministic arm decisions over a shared mergeability candidate pool and returns independently labeled arms for single-agent baseline, same-model multi-agent, hetero multi-reviewer, runtime-evidence floor, full supervisor stack, and oracle ceiling. The report must preserve replayable artifact references and observational trend rows without performing ledger writes.
PRD promise: P1, P2, P8
First public-boundary RED test: `test_powered_factorial_report_includes_all_labeled_arms`

Acceptance Criteria:
- [ ] All required arm labels appear with independent decision sources.
- [ ] Candidate-pool divergence is detected and reported as unavailable rather than compared.
- [ ] Replay artifact references and trend rows are present and observational only.

## Slice ISS-2: Compute Matched-TAR Metrics And Paired Counts

Type: AFK
Priority: P0
Estimate: M
Scope: Compute FAR, TAR, FRR, Wilson confidence intervals, paired discordant counts, matched true-accept status, and sample-size sufficiency for compared arms. The comparison must refuse to produce an improvement delta when candidate pools or true-accept rates do not match.
PRD promise: P2, P3
First public-boundary RED test: `test_matched_tar_refuses_unmatched_comparisons`

Acceptance Criteria:
- [ ] FAR, TAR, FRR, denominators, and confidence intervals are reported per arm.
- [ ] Matched-TAR comparison refuses unmatched rates with a clear status and reason.
- [ ] Paired discordant counts are computed from the shared candidate rows.

## Slice ISS-3: Add Reviewer Marginal Analysis

Type: AFK
Priority: P1
Estimate: M
Scope: Add leave-one-reviewer-out analysis using reviewer-level decisions where available. The report should quantify each reviewer’s marginal effect on full-stack acceptance, detect simple reviewer correlation or overlap where possible, and mark the analysis unavailable when required reviewer evidence is missing.
PRD promise: P5, P6
First public-boundary RED test: `test_leave_one_reviewer_out_records_marginal_effects_and_correlation`

Acceptance Criteria:
- [ ] Reviewer marginal effects are recorded for reviewers with decision rows.
- [ ] Reviewer correlation indicators are reported when enough paired reviewer evidence exists.
- [ ] Missing reviewer evidence blocks a full-stack claim, keeps the metric non-applyable, and produces no applyable proposal rather than being imputed.

## Slice ISS-4: Enforce Promotion Guardrails

Type: AFK
Priority: P0
Estimate: M
Scope: Keep oracle ceiling non-promotable, gaming-flagged reports non-applyable, reviewer-unavailable full-stack claims blocked, powered-threshold-unmet reports non-applyable, and policy mutation disabled even when a powered threshold is met. Confirm existing policy derivation refuses non-applyable or gaming-flagged reports.
PRD promise: P4, P6, P7, P8
First public-boundary RED test: `test_gaming_flagged_factorial_run_creates_no_applyable_proposal`

Acceptance Criteria:
- [ ] Oracle ceiling cannot be reported as supervisor improvement.
- [ ] Gaming flags produce no applyable policy proposal.
- [ ] Powered-threshold-met reports may mark metric evidence applyable but never mutate policy or advance gates.
