# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 871255 `prd_review`: Low-severity vacuous-green risk: P3 (S_full conjunction) and P5 (report-only invariants) are ALREADY-GREEN at HEAD 930bb722 (mergeability_bench.py:1925, :788-791/:1348-1366); downstream TDD gate must pin genuine net-new (produced paired_acceptance_report.json + Cursor isolation diagnostics per P2) rather than vacuously re-asserting existing invariants.
- event_id 871272 `prd_review`: both agents accepted
- event_id 871285 `issues_review`: gate blocked
- event_id 871734 `issues_review`: S1-AC5/AC7 and S2-AC1/AC2/AC3 describe already-green tested guards; TDD must pin net-new evidence (report artifact + cursor isolation diagnostics) to avoid vacuous-green.
- event_id 871734 `issues_review`: Minor downstream: tdd.md verification command #1 (test_configured_reviewer_panel_populates_full_gate_rows) names an absent test; sibling tests ground the seam so cosmetic, not an issues-gate defect.
- event_id 871735 `issues_review`: both agents accepted
- event_id 871834 `tdd_review`: Verification Commands name 0 of the 6 tests the plan describes; 4 named tests already exist and are green at HEAD 930bb722 (vacuous), and test_configured_reviewer_panel_populates_full_gate_rows does not exist anywhere (collection error). The executable contract cannot demonstrate the net-new diagnostic boundary.
- event_id 871835 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 871839 `tdd_review`: Verification Commands name 0 of the 6 tests the plan describes; 4 named tests already exist and are green at HEAD 930bb722 (vacuous), and test_configured_reviewer_panel_populates_full_gate_rows does not exist anywhere (collection error). The executable contract cannot demonstrate the net-new diagnostic boundary.
- event_id 871913 `tdd_review`: Verification Commands name 5 tests disjoint from the 6 net-new test_configured_full_panel_* tests in the plan body; running the gate is 4 vacuous-green + 1 collection error and never exercises the diagnostic boundary
- event_id 871913 `tdd_review`: FM-1.3 step repetition: same task id, same HEAD 930bb722, same tdd sha 54f92218, same defect unaddressed since prior REVISE
- event_id 871914 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 871921 `tdd_review`: Verification Commands name 5 tests disjoint from the 6 net-new test_configured_full_panel_* tests in the plan body; running the gate is 4 vacuous-green + 1 collection error and never exercises the diagnostic boundary
- event_id 871921 `tdd_review`: FM-1.3 step repetition: same task id, same HEAD 930bb722, same tdd sha 54f92218, same defect unaddressed since prior REVISE
- event_id 872004 `tdd_review`: Low severity: t2(cursor isolation)/t5(packet leak)/t6(invariants-false) partly re-assert already-green seams, mitigated by coupling to net-new diagnostic invocation and genuinely-new t1 report artifact
- event_id 872004 `tdd_review`: Low severity: handoff packet tdd sha field unchanged (54f92218) across all 3 rounds despite on-disk verif-command reconcile; shasum approval-blocked so fresh Read treated as authoritative
- event_id 872004 `tdd_review`: Low severity: pytest approval-blocked, test_status unknown
- event_id 872005 `tdd_review`: gate blocked
- event_id 872079 `tdd_review`: low-sev: tests 2/3/5/6 re-assert already-green seams (cursor isolation, missing-verdict unavailability, oracle leak detector, hardcoded-False invariants) but each coupled to net-new configured full-panel invocation; only test1 (paired_acceptance_report artifact write) is fully net-new behavior
- event_id 872079 `tdd_review`: low-sev: pytest+shasum approval-blocked so test_status=unknown and sha match not byte-verified; fresh Read of on-disk content authoritative
- event_id 872259 `tdd_review`: both agents accepted
- event_id 872283 `implementation_plan`: LOW-SEV: implementation-plan.md files-to-touch lists prd.md/issues.md/tdd.md which are mutable_by_worker:false; worker must not modify them
- event_id 872283 `implementation_plan`: LOW-SEV: plan is thin (~38L) and conditional, inheriting tdd GREEN-lean where t2/t3/t5/t6 re-assert already-green seams coupled to net-new configured invocation (only t1 produces a genuinely net-new report artifact)
- event_id 872465 `implementation_plan`: independent_reviewer_missing_verdict: independent-reviewer-0
- event_id 872471 `implementation_plan`: LOW-SEV: implementation-plan.md files-to-touch lists prd.md/issues.md/tdd.md which are mutable_by_worker:false; worker must not modify them
- event_id 872471 `implementation_plan`: LOW-SEV: plan is thin (~38L) and conditional, inheriting tdd GREEN-lean where t2/t3/t5/t6 re-assert already-green seams coupled to net-new configured invocation (only t1 produces a genuinely net-new report artifact)
- event_id 872492 `implementation_plan`: Low-sev: files-to-touch lists 3 immutable planning docs (prd/issues/tdd, mutable_by_worker:false) - worker must treat as no-op
- event_id 872492 `implementation_plan`: Low-sev: thin/conditional plan inherits tdd GREEN-lean coupling; t2/t3/t5/t6 re-assert already-green seams bound to net-new configured invocation (t1 genuinely net-new report artifact)
- event_id 872669 `implementation_plan`: both agents accepted
- event_id 873263 `execution`: gate blocked
- event_id 874098 `execution`: deliverable_evidence_failed: deliverable_evidence_failed: failures=accepted_gate_without_changed_files
- event_id 874365 `execution`: both agents accepted
- event_id 874533 `outcome_review`: Local pytest execution unavailable (tool approval-blocked); accept relies on static trace + handoff-reported 6/6 pass; supervisor runtime floor is authority and must rerun the 6 nodeids
- event_id 874925 `outcome_review`: both agents accepted
