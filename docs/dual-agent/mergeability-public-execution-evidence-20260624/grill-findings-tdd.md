# TDD Grill Findings

## Finding 1: Tests must inspect produced packets, not helper functions

Status: resolved.

The TDD plan starts at `run_paired_acceptance_pilot` and `swebench_mergeability_fixture_runner`, which are public report/runner seams.

## Finding 2: Reverse-classical status must not infer hidden oracle correctness

Status: resolved.

The test only asserts public candidate tests pass on the patched public worktree and fail on the original public worktree. It does not inspect hidden tests.

## Finding 3: SWE-bench evidence must survive the leak scanner

Status: resolved.

The TDD run caught forbidden key names inside excluded-material labels; the labels were rewritten to category names that do not trip oracle-reference scanning.

