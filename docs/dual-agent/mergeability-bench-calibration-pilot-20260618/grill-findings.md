# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 804461 `prd_review`: Low-med severity: run_paired_acceptance_pilot sets supervisor_accept=oracle_accept (mergeability_bench.py:611), making supervisor_false_accept structurally False (:621). Measures baseline gap from ground truth, not Supervisor independent verification skill. Non-blocking: runtime-native grader is the oracle on this held-out bench and Out-of-Scope line 35 disclaims production accuracy.
- event_id 804462 `prd_review`: both agents accepted
- event_id 804557 `issues_review`: low-sev: issues.md lacks reverse Coverage Index (forward per-slice mapping only; reconstructed reverse map confirms no orphans)
- event_id 804557 `issues_review`: low-sev: Slice 1 ACs under-enumerate vs TDD MBP-1 (no explicit AC for rejects_broken_known_good_control or saturated_all_one; property captured in Allowed/Forbidden outcomes and TDD)
- event_id 804558 `issues_review`: both agents accepted
- event_id 804677 `tdd_review`: LOW-SEV: TDD tests 9 (test_existing_mergeability_evaluator_quality_checks_remain_green) and 10 (test_paired_acceptance_report_cannot_create_applyable_policy_claim) diverge from impl names test_autoresearch_mergeability_evaluator_works_with_live_trials:410 and test_autoresearch_report_only_invariants_with_mergeability_evaluator:463; semantics realized verbatim (applyable_policy_proposal False:363/:679, derive==[]:508, report_contains_derivable False:509). Under-description not orphan.
- event_id 804677 `tdd_review`: LOW-SEV: MBP-3 tests 9/10 are GREEN-stays regression guards over pre-existing policy_evolution substrate rather than net-new RED, but explicitly intended as remain-green preservation of report-only property.
- event_id 805149 `tdd_review`: independent_reviewer_missing_verdict: independent-reviewer-0
- event_id 805155 `tdd_review`: LOW-SEV: TDD tests 9 (test_existing_mergeability_evaluator_quality_checks_remain_green) and 10 (test_paired_acceptance_report_cannot_create_applyable_policy_claim) diverge from impl names test_autoresearch_mergeability_evaluator_works_with_live_trials:410 and test_autoresearch_report_only_invariants_with_mergeability_evaluator:463; semantics realized verbatim (applyable_policy_proposal False:363/:679, derive==[]:508, report_contains_derivable False:509). Under-description not orphan.
- event_id 805155 `tdd_review`: LOW-SEV: MBP-3 tests 9/10 are GREEN-stays regression guards over pre-existing policy_evolution substrate rather than net-new RED, but explicitly intended as remain-green preservation of report-only property.
- event_id 805597 `tdd_review`: both agents accepted
- event_id 805689 `implementation_plan`: Low-sev: Files/Modules To Touch enumerates 3 of 12 candidate fixtures; the 9 new untracked fixtures (hidden_behavior_miss, hidden_edit, lint_build_failure, missing_regression_test, mutable_escape, overbroad_diff, secondary_rubric_only, tautological_test, wrong_test_path) are not listed though step 2 narrates twelve control cases. Plan traceability maps only 4 primary tests vs TDD's 10, and gives no reverse coverage index. Under-description, not a blocking defect.
- event_id 805991 `implementation_plan`: both agents accepted
- event_id 806123 `execution`: both agents accepted
- event_id 806306 `outcome_review`: Deliverable uncommitted (working-tree mods + untracked fixtures) and pytest could not run locally (Bash/py_compile approval denied) -> test_status=unknown; per handoff non-blocking, runtime floor reruns
- event_id 806306 `outcome_review`: 2 of 10 TDD tests (existing_evaluator_quality_remain_green:488, report_cannot_create_applyable_policy_claim:545) are GREEN-stays regression guards reusing pre-existing report-only helper, not net-new RED; net-new pilot safety covered by exports test:384
- event_id 806810 `outcome_review`: both agents accepted
