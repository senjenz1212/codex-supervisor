# TDD Grill Findings

## Decision

accept

### Finding 1: Start at the report boundary

status: resolved

Initial test planning could have started with helper-only packet serialization. Test 1 now exercises the public bridge report path while inspecting the public packet it emits.

### Finding 2: Reject missing S_probe substrate

status: resolved

S_probe substrate needed a negative test. Test 3 now rejects missing substrate before accepting the static public probe.

### Finding 3: Prove oracle timing directly

status: resolved

Oracle timing needed a direct proof. Test 4 now asserts frozen decision rows or hashes are recorded before oracle labels are attached.

### Finding 4: Prove reviewer non-imputation

status: resolved

Reviewer unavailability needed to prove non-imputation. Test 5 asserts S_full is unavailable and not accepted when the panel is missing.

### Finding 5: Preserve solve-rate adapter behavior

status: resolved

Solve-rate non-regression needed to remain explicit. Test 10 keeps the existing SWE-bench pass-at-k tests in scope so the bridge cannot repurpose those fields.

## Residual Risks

The tests will initially use fixture-shaped SWE-bench Pro records rather than live commercial split access. That is acceptable for this slice because live execution is explicitly out of scope, but the implementation should keep the interface honest enough for a later official-harness adapter.
