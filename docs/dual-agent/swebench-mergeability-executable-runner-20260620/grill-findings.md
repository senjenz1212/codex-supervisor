# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 830125 `prd_review`: Low-severity: the load-bearing novel claim (real patch apply, public command execution, public-worktree hidden-path exclusion, deferred oracle execution) is entirely net-new runner code unverifiable at PRD stage; existing bridge only validates s_probe_substrate statically (swe_bench_mergeability.py:110-143). PRD defers execution-isolation mechanism + protected_paths reuse to TDD/implplan - acceptable at PRD level but downstream gates must enforce.
- event_id 830126 `prd_review`: both agents accepted
- event_id 830154 `issues_review`: Low-sev (carried grill 830125): load-bearing claim of real patch-apply/command-exec/worktree-isolation/deferred-oracle is net-new runner code, unverifiable at issues stage
- event_id 830154 `issues_review`: Low-sev: no acceptance criterion pins HOW the oracle runs deterministically though intent names deterministic oracle grading; defer-pin to TDD
- event_id 830154 `issues_review`: Low-sev: S1 attributes all four PRD promises to itself, muddying attribution (P2/P3/P4 double-counted with S2/S3/S4)
- event_id 830154 `issues_review`: Low-sev: dependency waves not explicit (S1-first only implicit); S4-AC3 is regression GREEN-stays; no reverse Coverage Index
- event_id 830155 `issues_review`: both agents accepted
- event_id 830192 `tdd_review`: LOW-SEV: net-new is orchestration not execution; _run_command:3114, _copy_public_fixture_tree:1623, HIDDEN_ORACLE_KEYS:41-43, and 5 report-only invariants:504-508 pre-exist and are independently tested, so t6 is GREEN-leaning on invariant values and t1 reuses proven command execution; RED rests on the absent boundary
- event_id 830192 `tdd_review`: MINOR: t3/t6 double-attribute ISS-1/P2 region; no shasum verification (approval-blocked) so tdd.md sha ceb92466 confirmed by content read not hash
- event_id 830322 `tdd_review`: both agents accepted
- event_id 830350 `implementation_plan`: Low-sev: net-new value is orchestration not execution (cmd-exec/worktree-iso/denylist/5 invariants pre-exist and are independently tested).
- event_id 830350 `implementation_plan`: Low-sev: plan reuses _copy_public_fixture_tree/_run_command from mergeability_bench.py which is not in files-to-touch (import-reuse, unnamed).
- event_id 830350 `implementation_plan`: Low-sev: plan thin (~45L); fixture content (oracle/public data files) under-specified, only README listed; no reverse coverage index; linear steps without dependency waves.
- event_id 830486 `implementation_plan`: both agents accepted
- event_id 830597 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 830691 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 830888 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 830963 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 831075 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_tests_failed
- event_id 831263 `execution`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_changed_files_missing_from_diff
- event_id 831288 `execution`: both agents accepted
- event_id 831295 `outcome_review`: required_artifacts_missing
- event_id 831348 `outcome_review`: FM-1.3 step repetition applies: HEAD 32824da1 and the uncommitted diff (M supervisor/swe_bench_mergeability.py, M tests/test_swe_bench_pro_mergeability_bridge.py, A tests/fixtures/swe_bench_mergeability_fixture/README.md) are identical to a prior session's outcome_review acceptance; addressed by re-verifying from source this session rather than reusing the prior receipt
- event_id 831348 `outcome_review`: Deliverable remains UNCOMMITTED (working-tree only); not a blocker for outcome_review but flagged
- event_id 831348 `outcome_review`: Local pytest is approval-blocked so test execution was not performed this session; test_status=unknown and runtime floor must rerun
- event_id 831538 `outcome_review`: both agents accepted
