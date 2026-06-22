# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 842273 `prd_review`: P2/P4 do not quantify how many oracle-positive fixtures constitute success; PRD admits it does not solve statistical power, so success collapses to positives-increased + invariants-preserved. TDD must set concrete threshold.
- event_id 842273 `prd_review`: Naming ambiguity: measurement runner emits calibration_metric_applyable=true (mergeability_bench.py:774, =not bool(errors), computable) distinct from policy-gate metric_applyable=False (:1215). P5 means the policy-gate set; TDD must assert the correct flag.
- event_id 842273 `prd_review`: Diagnostic label/rationale tying added cases to Slice 1A is only in Implementation/Testing Decisions, not a numbered promise; minor.
- event_id 842274 `prd_review`: both agents accepted
- event_id 842283 `issues_review`: gate blocked
- event_id 842410 `issues_review`: S1-AC2 'increased oracle-positive diagnostic coverage' is unquantified (no numeric delta/baseline); TDD must pin a concrete before/after count
- event_id 842410 `issues_review`: GREEN-stays ACs (S1-AC3 isolation, S2-AC1 denominators/CIs, S2-AC2/AC4 invariants) are preservation not net-new; genuine net-new = fixture data + rationale field + S1-AC1 recording
- event_id 842410 `issues_review`: Minor: no reverse PRD-promise->slice index; P1 double-attributed to both slices
- event_id 842411 `issues_review`: both agents accepted
- event_id 842448 `tdd_review`: Low-sev: RED5 names policy-gate flags (metric_applyable:1215 hardcoded False) correctly but does not disambiguate from calibration_metric_applyable:774 which emits true in the summary; implementer must assert against the policy-gate report dict
- event_id 842448 `tdd_review`: Low-sev: 3 of 5 REDs are GREEN-leaning regression/characterization guards (RED2 isolation, RED4 denom/CI fields, RED5 invariants all pre-exist); net-new RED is corpus data + rationale field (RED1/RED3)
- event_id 842448 `tdd_review`: Minor: RED1 is multi-mapped to 4 promises in one integration test; all per-promise assertions must be present. No reverse promise->test index in the tdd doc.
- event_id 842661 `tdd_review`: both agents accepted
- event_id 842696 `implementation_plan`: Low severity: RED5/RED3/RED2 assert behavior already present in source (invariants hardcoded False:1215-1233, control/trap validation:506-631/654, leak detector pre-exists); genuine net-new is diagnostic fixture data, growth rationale field, and positive denominator >3
- event_id 842696 `implementation_plan`: Minor: prd.md/tdd.md listed as files-to-touch though planning artifacts are mutable_by_worker:false in handoff
- event_id 842696 `implementation_plan`: Minor: plan is thin (~32 lines) with no per-step file ownership, no explicit dependency-wave, no reverse test->step coverage index
- event_id 842853 `implementation_plan`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 842859 `implementation_plan`: Low severity: RED5/RED3/RED2 assert behavior already present in source (invariants hardcoded False:1215-1233, control/trap validation:506-631/654, leak detector pre-exists); genuine net-new is diagnostic fixture data, growth rationale field, and positive denominator >3
- event_id 842859 `implementation_plan`: Minor: prd.md/tdd.md listed as files-to-touch though planning artifacts are mutable_by_worker:false in handoff
- event_id 842859 `implementation_plan`: Minor: plan is thin (~32 lines) with no per-step file ownership, no explicit dependency-wave, no reverse test->step coverage index
- event_id 842930 `implementation_plan`: MEDIUM: Files/Modules To Touch is materially incomplete - it omits tests/fixtures/mergeability_bench/{candidates,repos,tasks} (verified to exist with 18 candidate files), the exact location where adding diagnostic oracle-positive fixtures must occur. The plan cannot be audited for the corpus-growth work it gates.
- event_id 842930 `implementation_plan`: MINOR: prd.md and tdd.md are listed as files-to-touch but are mutable_by_worker:false in the handoff; worker-edit scope must drop them.
- event_id 842930 `implementation_plan`: MINOR: Step 1 does not pin the Slice 1A input report path (.scratch/mergeability-fixture-panel-produced-baseline-measurement-20260621/paired_acceptance_report.json) or the expected scratch output artifact path.
- event_id 842930 `implementation_plan`: LOW: denominator target is implicit; should state n_good > 3 explicitly while preserving controls and report-only flags.
- event_id 842930 `implementation_plan`: LOW (carried, non-blocking): RED5/RED3/RED2 are GREEN-leaning guards over pre-existing behavior; genuine net-new concentrates in fixture data + growth-rationale field + denominator>3.
- event_id 842931 `implementation_plan`: agents have not both accepted yet; revise and continue
- event_id 842933 `implementation_plan`: MEDIUM: Files/Modules To Touch is materially incomplete - it omits tests/fixtures/mergeability_bench/{candidates,repos,tasks} (verified to exist with 18 candidate files), the exact location where adding diagnostic oracle-positive fixtures must occur. The plan cannot be audited for the corpus-growth work it gates.
- event_id 842933 `implementation_plan`: MINOR: prd.md and tdd.md are listed as files-to-touch but are mutable_by_worker:false in the handoff; worker-edit scope must drop them.
- event_id 842933 `implementation_plan`: MINOR: Step 1 does not pin the Slice 1A input report path (.scratch/mergeability-fixture-panel-produced-baseline-measurement-20260621/paired_acceptance_report.json) or the expected scratch output artifact path.
- event_id 842933 `implementation_plan`: LOW: denominator target is implicit; should state n_good > 3 explicitly while preserving controls and report-only flags.
- event_id 842933 `implementation_plan`: LOW (carried, non-blocking): RED5/RED3/RED2 are GREEN-leaning guards over pre-existing behavior; genuine net-new concentrates in fixture data + growth-rationale field + denominator>3.
- event_id 842957 `implementation_plan`: LOW: Step 1 names 'the Slice 1A persisted report' without pinning the exact input path (.scratch/mergeability-fixture-panel-produced-baseline-measurement-20260621/paired_acceptance_report.json); non-blocking
- event_id 842957 `implementation_plan`: LOW: denominator n_good>3 lives in TDD RED1 but is not echoed numerically in the plan body
- event_id 843095 `implementation_plan`: both agents accepted
- event_id 843249 `execution`: gate blocked
- event_id 843545 `execution`: gate blocked
- event_id 843720 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 843910 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 844036 `execution`: deliverable_evidence_failed: deliverable_evidence_failed: failures=accepted_gate_without_changed_files
- event_id 844067 `execution`: both agents accepted
- event_id 844214 `outcome_review`: both agents accepted
