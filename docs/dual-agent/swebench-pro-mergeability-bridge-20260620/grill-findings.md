# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 826718 `prd_review`: gate blocked
- event_id 827197 `prd_review`: Low severity, carry into TDD: PRD's reuse of existing oracle-isolation scanning is incomplete -- _public_input_oracle_refs denylist at mergeability_bench.py:66-71 ({expected_outcome,final_score,oracle_accept,hidden_test_commands}) does not include the SWE-bench Pro oracle fields FAIL_TO_PASS/PASS_TO_PASS/test_patch that P3/P5/story-3 require excluded; the denylist must be extended or the oracle-isolation proof could pass while a Pro-named field leaks
- event_id 827197 `prd_review`: Minor: FRR helper not explicitly present at HEAD (derivable from FAR/TAR); S_probe patch-applicability substrate is net-new (PRD permits fixtures, acceptable at PRD level)
- event_id 827198 `prd_review`: both agents accepted
- event_id 827373 `issues_review`: Low-sev (carry to TDD, not block): denylist-extension gap persists from prd_review - ORACLE_REVIEW_FORBIDDEN_KEYS:66-71 lacks the three SWE-bench Pro oracle field names that S1-AC2/AC3 require to trigger isolation failure
- event_id 827373 `issues_review`: Low-sev: P7 double-attributed to S1 (weak) and S4 (owner)
- event_id 827373 `issues_review`: Low-sev: P5 confidence-intervals named in S3 scope but no dedicated AC
- event_id 827373 `issues_review`: Low-sev: no reverse PRD-promise->slice coverage index in the doc (derived during review)
- event_id 827374 `issues_review`: both agents accepted
- event_id 827542 `tdd_review`: Carried denylist-extension objection only partially pinned: Test 2 should specify FAIL_TO_PASS/test_patch as the injected forbidden key so the defense-in-depth scanner is forced to extend; otherwise Test 2 passes GREEN via final_score while ORACLE_REVIEW_FORBIDDEN_KEYS:66-71 stays blind to SWE-bench Pro names (low sev, mitigated by Test 1 builder-whitelist exclusion).
- event_id 827542 `tdd_review`: t10/t11 are GREEN-stays non-regression guards not genuine new RED (2 of 11).
- event_id 827542 `tdd_review`: S4-AC2 (documentation distinguishes solve-rate from FAR/TAR) weakly testable; t10 does not assert documentation.
- event_id 827542 `tdd_review`: No reverse coverage index in tdd.md (derived during review).
- event_id 827959 `tdd_review`: both agents accepted
- event_id 828023 `implementation_plan`: Low-sev carried: Step 1 leak-detection + Risk 2 allowlist give good primary defense but plan never names extending ORACLE_REVIEW_FORBIDDEN_KEYS with SWE-Pro fields; detector + TDD Test 2 remain blind to FAIL_TO_PASS/test_patch
- event_id 828023 `implementation_plan`: Minor: 11th TDD test test_existing_mergeability_behavior_remains_green omitted from plan traceability table (covered in prose Step 6/Verification, P4/P5/P6 covered by other tests)
- event_id 828023 `implementation_plan`: Minor: module file name swe_bench_mergeability.py does not echo pro/bridge of boundary symbol swebench_pro_mergeability_bridge_report (cosmetic)
- event_id 828023 `implementation_plan`: Minor: no reverse coverage index, no per-slice->file ownership map, no explicit build-wave DAG (Steps linearly ordered imply wave)
- event_id 828221 `implementation_plan`: both agents accepted
- event_id 828350 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 828419 `execution`: both agents accepted
- event_id 828450 `outcome_review`: Deliverable is uncommitted (two untracked files); supervisor must commit/track before merge
- event_id 828450 `outcome_review`: pytest approval-blocked locally: no tests-passed claim made; runtime floor is authority
- event_id 828450 `outcome_review`: t8 proposals==[] is partly trivial (bridge report has no 'records' key so loop is empty) in addition to the metric_applyable guard
- event_id 828450 `outcome_review`: oracle_ceiling arm FAR is tautologically 0 since oracle_ceiling_accept==oracle_accept (acceptable for coupled report-only arm)
- event_id 828881 `outcome_review`: both agents accepted
