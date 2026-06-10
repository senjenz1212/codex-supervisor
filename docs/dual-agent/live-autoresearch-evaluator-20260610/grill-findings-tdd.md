# TDD Grill Findings

## Translation Audit

- Every PRD promise has at least one issue claimant.
- The first RED tests exercise public boundaries: `validate_attempt(...)`, `run_autoresearch_fixture(...)`, and the CLI.
- Negative coverage includes fixture-only metrics, evaluator hash mismatch, dangling evidence refs, mutable path escape, and live mode without explicit opt-in.
- Report-only invariants are tested on live output rather than inferred from fixture replay.

## Findings

### T1 - Helper-Only Risk

- Risk: testing only an evaluator helper could miss the orchestrator and ledger event behavior.
- Resolution: primary tests use `run_autoresearch_fixture(...)` and the CLI; helper tests are allowed only for setup.

### T2 - Mutable Escape Must Check The Source Checkout

- Risk: an evaluator could write outside `mutable_paths` and the test could only inspect the temporary worktree.
- Resolution: escape tests must assert the source checkout target did not change.

### T3 - Hash Mismatch Needs Pre-Execution Proof

- Risk: a test that only sees validation rejection cannot prove a mismatched evaluator did not run.
- Resolution: mismatch test uses an evaluator that would create a marker if executed, then asserts the marker is absent.

### T4 - Zero Variance Should Be Review Evidence, Not Automatic Denial

- Risk: making zero variance fatal penalizes deterministic evaluators.
- Resolution: test asserts flag presence while preserving acceptance if no other errors exist.

## Gate Result

Resolved. The TDD plan can proceed to implementation.
