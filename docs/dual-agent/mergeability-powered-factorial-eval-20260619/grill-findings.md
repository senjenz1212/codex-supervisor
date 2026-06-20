# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 824835 `prd_review`: LOW SEVERITY: P7 permits metric_applyable=true on powered-threshold-met, the first loosening of a previously hardcoded-False guard (mergeability_bench.py:1004); the powered-threshold definition is unspecified in the PRD and must be pinned precisely in the TDD gate. Bounded acceptably by P6 (false-keeping conditions) and P7/P8 (default_change/policy_mutated/gate_advanced stay false) plus OOS report-only fence.
- event_id 824835 `prd_review`: LOW SEVERITY: P5 leave-one-reviewer-out and reviewer-correlation indicators have no existing seam (count=0); they extend panel_marginal_delta_at_matched_true_accept:986 and include an unavailable-not-fabricated guard, so additive surface is reasonable but unproven until TDD.
- event_id 824836 `prd_review`: both agents accepted
- event_id 824889 `issues_review`: Low severity: ISS-3 collapses reviewer marginal-effect and correlation-indicator into one test (test_leave_one_reviewer_out_records_marginal_effects_and_correlation), consistent with TDD but not separately enumerated
- event_id 824889 `issues_review`: Low severity: PRD promise P2 is attributed to both ISS-1 (candidate-pool divergence -> unavailable) and ISS-2 (paired discordant counts); coherent split, not an orphan
- event_id 824889 `issues_review`: Low severity: no reverse coverage-index mapping slice->test->promise (recurring accepted pattern); pytest unrun since named tests do not yet exist
- event_id 824890 `issues_review`: both agents accepted
- event_id 824937 `tdd_review`: Low-sev: t8 (policy-derivation refusal pre-exists policy_evolution:496-507) and t9 (metric_applyable hardcoded False:1004) are GREEN-leaning; their RED comes only from absence of new factorial report fn
- event_id 824937 `tdd_review`: Low-sev: t6 folds reviewer marginal-effect and correlation into a single test (consistent with issues ISS-3)
- event_id 824937 `tdd_review`: Low-sev: no reverse coverage-index table (recurring accepted pattern)
- event_id 825108 `tdd_review`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 825114 `tdd_review`: Low-sev: t8 (policy-derivation refusal pre-exists policy_evolution:496-507) and t9 (metric_applyable hardcoded False:1004) are GREEN-leaning; their RED comes only from absence of new factorial report fn
- event_id 825114 `tdd_review`: Low-sev: t6 folds reviewer marginal-effect and correlation into a single test (consistent with issues ISS-3)
- event_id 825114 `tdd_review`: Low-sev: no reverse coverage-index table (recurring accepted pattern)
- event_id 825152 `tdd_review`: FM-1.3 applies: tdd.md sha 0e9dd294 + HEAD 267d75cc identical to R1 ACCEPT (Read byte-match); independent-reviewer-1 non-accept means re-accept repeats the handoff
- event_id 825152 `tdd_review`: Headline P5 test t6 conflates 3 assertions; pre-existing _per_reviewer_metrics:635 risks partial-GREEN without true leave-one-out
- event_id 825152 `tdd_review`: t8/t9 lean GREEN-stays (policy refusal + metric_applyable hardcoded False pre-exist)
- event_id 825152 `tdd_review`: Literal independent-reviewer-1 objection text absent from handoff; inferred and disclosed (FM-2.4)
- event_id 825153 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 825155 `tdd_review`: FM-1.3 applies: tdd.md sha 0e9dd294 + HEAD 267d75cc identical to R1 ACCEPT (Read byte-match); independent-reviewer-1 non-accept means re-accept repeats the handoff
- event_id 825155 `tdd_review`: Headline P5 test t6 conflates 3 assertions; pre-existing _per_reviewer_metrics:635 risks partial-GREEN without true leave-one-out
- event_id 825155 `tdd_review`: t8/t9 lean GREEN-stays (policy refusal + metric_applyable hardcoded False pre-exist)
- event_id 825155 `tdd_review`: Literal independent-reviewer-1 objection text absent from handoff; inferred and disclosed (FM-2.4)
- event_id 825198 `tdd_review`: LOW: t6 test_leave_one_reviewer_out_records_marginal_effects_and_correlation folds three assertions into one test; pre-existing panel-level _per_reviewer_metrics risks partial-GREEN. Non-blocking: artifact is mutable_by_worker:false and RED is genuine.
- event_id 825343 `tdd_review`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 825349 `tdd_review`: LOW: t6 test_leave_one_reviewer_out_records_marginal_effects_and_correlation folds three assertions into one test; pre-existing panel-level _per_reviewer_metrics risks partial-GREEN. Non-blocking: artifact is mutable_by_worker:false and RED is genuine.
- event_id 825407 `tdd_review`: Low-severity (non-blocking): t8 (policy-refusal) and t9 (metric_applyable=false) are GREEN-stays regression guards over pre-existing logic at policy_evolution.py:500-507, not net-new RED; t6 folds marginal-effect and reviewer-correlation into one test (acceptable since both serve single promise P5); no reverse Coverage Index in the plan.
- event_id 825407 `tdd_review`: FM-1.3: tdd.md 0e9dd294 + HEAD 267d75cc are identical to prior rounds; repetition proven but not the sole basis for this decision.
- event_id 825408 `tdd_review`: gate blocked
- event_id 825512 `tdd_review`: t6 conflates reviewer marginal-effect and correlation into a single test (granularity nit; tdd immutable by worker)
- event_id 825512 `tdd_review`: t8/t9 are GREEN-leaning: their RED depends on the not-yet-existing factorial report function rather than a newly added guard
- event_id 825512 `tdd_review`: pytest not run (planning gate); verification is static trace only
- event_id 825776 `tdd_review`: LOW: RED is genuine only against committed HEAD 267d75cc; worker pre-implemented the full module and all 11 tests in the working tree, so tests are GREEN-now and first-RED discipline is not live-observable (known low-severity workflow pattern).
- event_id 825776 `tdd_review`: LOW: t6 test_leave_one_reviewer_out_records_marginal_effects_and_correlation folds two P5 sub-promises (marginal effect + reviewer correlation) into one test; granularity nit, tdd_plan is mutable_by_worker=false.
- event_id 825776 `tdd_review`: LOW: on-disk->handoff sha binding (8145ae42) could not be independently confirmed because shasum was approval-blocked; review relied on Read-verified file content instead.
- event_id 825890 `tdd_review`: both agents accepted
- event_id 826018 `implementation_plan`: both agents accepted
- event_id 826064 `execution`: both agents accepted
- event_id 826222 `outcome_review`: both agents accepted
