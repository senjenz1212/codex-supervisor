# PRD Grill Findings

## Finding 1: Public Substrate Could Overclaim

Status: resolved in PRD and tests.

The replay runner must label a no-public-test replay as static/lint-only rather than implying parity with hidden SWE-bench oracle tests. The PRD now requires explicit substrate labeling and public-boundary tests will assert the label.

## Finding 2: Patch Text Must Be Real Evidence

Status: resolved in PRD and tests.

Existing fixture candidates rely on `patch_operations`, which is useful for deterministic unit fixtures but not enough for SWE-bench-shaped replay artifacts. The PRD now requires model patch text application and deterministic apply failure receipts.

## Finding 3: Reviewer Panel Availability Must Stay Honest

Status: resolved in PRD and tests.

S_full must not silently inherit S_probe when configured reviewers are missing or unavailable. The PRD keeps the reviewer adapter injectable, defaults to unavailable, and requires tests for unavailable panel behavior.
