# Independent Reviewer Cursor Primary TDD Grill Findings

## Findings

### Finding T1: Tests must cover hang behavior without hanging

status: resolved
severity: high
question: Can the timeout regression test use the live Cursor SDK?
resolution: No. The TDD plan requires a deterministic fake runner or patched
wait path that simulates a blocking Cursor SDK call and proves the supervisor
returns a recoverable infrastructure failure without sleeping for the full live
timeout.

### Finding T2: Fallback tests must include rejection semantics

status: resolved
severity: high
question: Could fallback success tests accidentally weaken gates?
resolution: The TDD plan requires fallback `revise` and real `deny` cases to
prove valid verdicts block regardless of runtime.

### Finding T3: Rename tests must prevent provenance drift

status: resolved
severity: medium
question: Is adding `independent_reviewer` enough by itself?
resolution: No. The TDD plan requires both alias-preservation and no-Cursor-label
tests for `litellm_structured`.

### Finding T4: Live probes must not become suite dependencies

status: resolved
severity: medium
question: Should live Cursor/Gemini credentials be required for the test suite?
resolution: No. The TDD plan keeps live evidence as artifacts and requires
deterministic tests with fake model/SDK boundaries.

## Decision

No open TDD grill findings remain.
