# TDD Grill Findings

Gate: `tdd_review`

Decision: accepted after revisions below.

## Findings

### T1. The first RED must not be helper-only

Finding: A helper test around "strip overlay" would not prove the public evaluator boundary measures the floor.

Resolution: Cycle 1 starts at `run_evaluator_trials` and executes a real evaluator subprocess. The strip operation remains below the public boundary.

Status: resolved

### T2. Durable resume must not drop the measured floor

Finding: P2 can appear green if only the in-memory execution result is tested. The durable job terminal payload must also preserve before/after/delta.

Resolution: Cycle 2 asserts through `run_autoresearch_fixture`, which uses `run_durable_evaluator_trials` and durable terminal payload helpers.

Status: resolved

### T3. Draft proposal must require quality controls

Finding: P3 cannot be proven by manually injecting `empty_floor_comparison` into a report fixture. That would violate the live-to-derivation promise.

Resolution: Cycle 3 uses a live evaluator fixture that produces policy overlay candidate evidence and evaluator-quality controls, then calls the existing deriver through the orchestrator report.

Status: resolved

### T4. Authority invariants need direct assertions

Finding: It is easy to prove "draft exists" while missing that the proposal became too powerful.

Resolution: Cycle 3 directly asserts operator approval remains required, default changes remain disallowed, automatic mutation is false, gates do not advance, and the live overlay file is unchanged.

Status: resolved

## Gate Receipt

`tdd_review` accepted. All named tests hit public boundaries, preserve forbidden outcomes, and use live infra fakes below the public boundary only.
