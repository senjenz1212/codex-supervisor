# TDD Grill Findings

Task id: `pro-oracle-gold-proof-20260626`

## Verdict

Accepted after clarifying that live Docker is an artifact-producing execution step, not a pytest boundary.

## Findings

### T1. The first RED must not run live Docker.

Status: resolved.

Evidence: The user explicitly scoped real Docker as empirical execution evidence. Running it in pytest would make the suite non-hermetic and fragile.

Resolution: RED 1 reads a committed real receipt fixture. The live VM command is documented separately as an execution step.

### T2. The empty-output guard must cross the adapter boundary.

Status: resolved.

Evidence: Testing `_pro_passed_tests(...)` alone would be too low-level and could miss the adapter's status/receipt contract.

Resolution: RED 2 calls `run_swe_bench_pro_oracle(...)` and fakes Docker below that seam, matching existing test style.

### T3. The plan must preserve ordinary oracle-bad candidates.

Status: resolved.

Evidence: A non-empty parser output with required failed tests is a legitimate oracle-bad outcome for future corpus generation.

Resolution: Minimal GREEN only marks empty parser output with nonzero test command as unavailable. Existing non-empty fail classification remains valid.

### T4. The receipt fixture test must inspect non-empty parsed tests, not just top-level pass/pass.

Status: resolved.

Evidence: A top-level pass/pass fixture could be hand-written without proving parser output.

Resolution: RED 1 requires non-empty parsed tests from the committed output artifact in addition to pass/pass receipt fields.

### T5. The receipt fixture must inspect raw patch-apply and required bucket evidence.

Status: resolved after tri-agent review.

Evidence: Top-level `patch_applied=true` can be stamped by the adapter success path even when `patch_apply.json` is missing unless implementation is fixed.

Resolution: RED 1 requires raw patch-apply proof and non-empty `fail_to_pass`, `pass_to_pass`, and selected tests.
