# TDD Grill Findings

## Finding 1: Tests Must Hit The Live Boundary

Status: resolved.

The TDD plan starts with `swebench_mergeability_live_runner` and CLI live mode rather than private helper tests.

## Finding 2: Replay Ordering Must Be Reused

Status: resolved.

The plan requires the live runner to delegate to replay and verify frozen decisions are written before oracle outputs.

## Finding 3: Budget Failure Must Not Look Like A Negative Candidate

Status: resolved.

The plan distinguishes unavailable budget-overrun runs from accepted or rejected candidate results, preventing budget failures from entering FAR/TAR as valid supervisor behavior.
