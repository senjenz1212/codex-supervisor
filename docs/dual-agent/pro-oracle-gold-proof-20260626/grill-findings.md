# PRD Grill Findings

Task id: `pro-oracle-gold-proof-20260626`

## Verdict

Accepted after tightening the PRD around two risks: a real Docker receipt must be empirical outcome evidence, and empty parser output must fail closed as unavailable rather than ordinary fail/pass.

## Findings

### G1. Gold proof can be invalid if `before_repo_set_cmd` erases the candidate patch.

Status: resolved in PRD.

Evidence: `run_swe_bench_pro_oracle(...)` writes the patch and entryscript at `supervisor/swe_bench_official_oracle.py:368-385`. The current entryscript applies the patch before appending `before_repo_set_cmd` at `supervisor/swe_bench_official_oracle.py:838-847`. SWE-bench Pro rows can include `git reset --hard` in `before_repo_set_cmd`, so the real proof must allow an adapter-ordering fix if the VM run shows the patch is erased before tests.

Resolution: P1 allowed outcomes explicitly permit adapter execution-order fixes needed for the gold patch to survive until test execution.

### G2. Empty parsed tests must not be treated as a legitimate oracle-negative result.

Status: resolved in PRD.

Evidence: `_pro_passed_tests(...)` currently accepts an empty tests list and returns an empty set at `supervisor/swe_bench_official_oracle.py:930-947`; the subset check at `supervisor/swe_bench_official_oracle.py:535-536` then classifies required tests as fail. That is unsafe for crash-shaped `{"tests":[]}` output.

Resolution: P2 makes empty parser output with nonzero test command a public-boundary guard and forbids treating it as ordinary fail/pass.

### G3. The real VM run must not become a unit-test dependency.

Status: resolved in PRD.

Evidence: The user explicitly scoped real Docker execution as execution/outcome evidence, not a RED pytest. The repo already fakes Docker below `run_swe_bench_pro_oracle(...)` in existing tests.

Resolution: Testing decisions forbid pytest from running real Docker; the live proof is a committed artifact.

### G4. This proof is not a benchmark or auto-evolve artifact.

Status: resolved in PRD.

Evidence: The all-arms runner is report-only and authority flags stay false in `supervisor/swe_bench_mergeability.py:3296-3305`.

Resolution: Out of scope and Promise Contracts keep all benchmark, powered-stat, panel, and policy bridge work excluded.

### G5. Patch application proof must be raw and fail-closed.

Status: resolved after tri-agent review.

Evidence: `_pro_patch_applied(...)` returns `None` for missing/malformed receipts at `supervisor/swe_bench_official_oracle.py:787-797`, while the caller currently only blocks on `False`.

Resolution: The PRD and TDD now require raw `patch_apply.json.patch_applied is true`; implementation must mark missing/malformed patch-apply proof unavailable.

### G6. The proof runner must be deterministic.

Status: resolved after tri-agent review.

Evidence: The adapter only treats `instance_id`, `candidate_id`, `model_patch`, and `base_commit` as missing context, while empty `fail_to_pass` and `pass_to_pass` buckets could make subset checks vacuous.

Resolution: The PRD now requires a deterministic runner/runbook that validates non-empty required buckets and selected tests before invoking the adapter.
