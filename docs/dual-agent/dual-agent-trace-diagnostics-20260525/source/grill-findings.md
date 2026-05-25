# Grill Findings

## Finding 1

status: resolved

Concern: Changing `category` values to match MAST directly could break existing
tests and downstream consumers that expect `task_verification`, `governance`,
`tool_execution`, and `system_design`.

Resolution: Preserve existing supervisor fields and add `mast_code`,
`mast_mode`, and `mast_category` as additive fields.

## Finding 2

status: resolved

Concern: Some MAST modes require cross-event analysis and should not be faked
inside single-event classification.

Resolution: Keep `classify_failure` reason-based and add a separate replay
sequence detection path for duplicate gate inputs, ignored input, premature
termination, and incorrect verification.

## Finding 3

status: resolved

Concern: Per-tool timing can become misleading if synthetic timings are mixed
with real model-call timings without distinction.

Resolution: Real invocation boundaries use a timing context manager. Normalized
or synthetic records still receive the required fields, and tests only require
real non-zero timing for owned invocation boundaries.

## Finding 4

status: resolved

Concern: Cursor must not become the acceptance boundary.

Resolution: Cursor disagreement is classified as evidence for diagnosis. Codex
and the supervisor remain responsible for final gate policy.
