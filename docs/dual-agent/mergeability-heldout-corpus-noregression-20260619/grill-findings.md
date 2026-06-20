# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 818867 `prd_review`: gate blocked
- event_id 818989 `prd_review`: both agents accepted
- event_id 818998 `issues_review`: gate blocked
- event_id 819091 `issues_review`: P2 under-attributed: held-out/dev report separation is in S2 scope but P2 is listed only under S1 (covered, not orphaned)
- event_id 819091 `issues_review`: S1-AC4 (split metadata in manifest+exports) has no dedicated named test in this task's TDD; relies on inherited _heldout_coverage_from_manifest:573 + export tests
- event_id 819091 `issues_review`: No reverse Coverage Index (issues->PRD given; PRD->issues table absent)
- event_id 819091 `issues_review`: RED-genuineness moot: working-tree code already implements all slices (outcome concern, not issues-decomposition flaw)
- event_id 819092 `issues_review`: both agents accepted
- event_id 819207 `tdd_review`: RED-genuineness is moot at the working-tree state: worker pre-implemented both supervisor/mergeability_bench.py and tests/test_mergeability_bench.py (both modified), so all 6 tests are GREEN now; RED confirmed only as net-new vs committed HEAD 66993f26, not an observed RED->GREEN transition
- event_id 819207 `tdd_review`: P6 (calibration non-applyable / cannot mutate policy/advance gates/create proposals) is under-attributed: t6 covers metric_applyable/improvement_claim_allowed/heldout_improvement False, but full policy-authority closure (default_change_allowed/policy_mutated/gate_advanced False + derive()==[] + report_contains_derivable False) lives in sibling test_heldout_no_regression_report_remains_non_applyable:1091, not in this plan's 6 named tests
- event_id 819207 `tdd_review`: No reverse Coverage Index (test->PRD mapping given; no PRD->test table)
- event_id 819433 `tdd_review`: both agents accepted
- event_id 819446 `implementation_plan`: gate blocked
- event_id 819596 `implementation_plan`: Low severity: plan provides no reverse Coverage Index (test->file ownership) and no explicit dependency-wave map; mitigated by single-source/single-test/single-fixture scope and build-correct step order
- event_id 819596 `implementation_plan`: Low severity: test_heldout_no_regression_report_remains_non_applyable pre-exists at HEAD:979 as a report-only regression guard, so its P6 attribution is partly GREEN-stays not fresh RED (new behavior covered by t5/t7)
- event_id 819768 `implementation_plan`: both agents accepted
- event_id 820012 `execution`: gate blocked
- event_id 820197 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 820259 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 820410 `execution`: both agents accepted
- event_id 820417 `outcome_review`: required_artifacts_missing
- event_id 820491 `outcome_review`: Deliverable is uncommitted (M supervisor/mergeability_bench.py, M tests/test_mergeability_bench.py, untracked fixtures); pytest approval-blocked locally so all 7 tests are GREEN-by-construction, not observed RED->GREEN. test_status=unknown; runtime floor is authority.
- event_id 820491 `outcome_review`: is_no_regression_failure was inverted from the committed HEAD definition (base+/oracle- -> base+/oracle+/NOT-full-gate); intentional per task intent and test name but it replaces the prior committed no-regression meaning, so any external consumer of that field gets different findings.
- event_id 820491 `outcome_review`: Contract test test_heldout_no_regression_report_remains_non_applyable pre-existed at HEAD:979 (GREEN-stays guard modified for new semantics); only 6/7 are genuinely net-new RED.
- event_id 820654 `outcome_review`: both agents accepted
