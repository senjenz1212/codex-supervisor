# TDD Grill Findings

Task id: `pro-corpus-generate-label-20260626`

## Finding 1 - First RED must hit the builder boundary

Status: resolved.

Concern: A loader-only test would repeat the previous slice and miss the generated-attempt plus gold-backstop contract.

Resolution: Cycle 1 starts at `build_swe_bench_pro_candidate_corpus(...)`.

## Finding 2 - Fake oracle is allowed only below the seam

Status: resolved.

Concern: The unit test might mock the produced corpus itself.

Resolution: The tests call the real builder and fake only `oracle_runner(attempt)` below the public seam.

## Finding 3 - `n_bad` must be checked directly

Status: resolved.

Concern: A test that only asserts `false_accept_rate == 0.0` would be tautological because `_rate(..., 0)` also returns zero.

Resolution: Cycle 2 asserts `n_bad`, `false_accept_denominator`, and `far_degenerate`.

## Finding 4 - Live evidence is not a unit test

Status: resolved.

Concern: Running Docker or a real model in pytest would make the suite environment-dependent.

Resolution: Cycle 4 makes real execution a committed artifact and keeps unit tests faked below the boundary.

## Decision

Accept TDD after revisions. The plan proves the public seam and keeps live evidence separate.
