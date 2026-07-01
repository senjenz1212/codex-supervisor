# TDD Grill Findings

Task id: `pro-oracle-resolution-fidelity-20260630`

## Finding 1: First RED must hit the adapter boundary, not a helper.

Concern: A helper-only test could pass while `run_swe_bench_pro_oracle` still reports unavailable for empty `PASS_TO_PASS`.

Resolution: The first RED calls `run_swe_bench_pro_oracle` with Docker faked below the boundary.

Status: resolved.

## Finding 2: rc-nonzero must not become a broad bypass.

Concern: The TDD plan could check only the positive rc-nonzero case and miss empty parser output or failed status.

Resolution: The issue pack includes a negative rc-fail-closed slice, and the implementation must keep `patch_applied`, `tests_count`, and parsed status checks.

Status: resolved.

## Finding 3: Disclosure propagation must be tested through public report surfaces.

Concern: Testing only `_interpret_oracle_outcome` would not prove JSONL rows or summaries contain the disclosure.

Resolution: The corpus test starts at `build_swe_bench_pro_candidate_corpus` and then loads/summarizes the written JSONL. The powered test starts at the DoD checker.

Status: resolved.

## Finding 4: Phase 0 rerun must remain execution evidence.

Concern: A unit test that pretends to rerun the VM curation would be fake confidence.

Resolution: The TDD plan keeps the rerun as an evidence artifact after focused tests pass.

Status: resolved.
