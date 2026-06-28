# TDD Grill Findings

Task id: `pro-single-agent-solver-20260626`

## Verdict

Accepted after requiring the first RED to invoke the generator via the env-var public boundary and keeping live model execution outside pytest.

## Findings

### T1. The first RED must cross the `_command_generator` contract shape.

Status: resolved.

Evidence: The CLI seam shells out and communicates only through `SWEBENCH_MERGEABILITY_GENERATOR_INPUT` and `SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT`.

Resolution: RED 1 invokes `swe_bench_solver.main(...)` with those env vars instead of testing only helper functions.

### T2. The fake runner must sit below the generator boundary.

Status: resolved.

Evidence: Unit tests cannot spend live model budget, but bypassing the generator would miss packet validation, k-loop, diff capture, and receipt output.

Resolution: Tests inject a fake runner/callable or runner command below the solver implementation while keeping the solver boundary real.

### T3. Receipt tests must use the production resolver.

Status: resolved.

Evidence: `_resolve_powered_baseline_decision(...)` is the trust gate for produced baseline receipts.

Resolution: RED 2 and RED 3 call the resolver directly on generator-emitted receipts.

### T4. Non-resolving retention must be tested by generator output, not oracle results.

Status: resolved.

Evidence: The generator should preserve attempts before oracle labeling; oracle-good/bad belongs to the corpus builder.

Resolution: RED 1 asserts all k attempts exist regardless of `accept` values.
