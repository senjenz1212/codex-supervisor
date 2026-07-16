# TDD Plan: TRACER-001

## Current State

The dedicated hermetic suite is implemented and green:

```text
uv run pytest -q tests/test_tracer_001_e2e.py
3 passed in 5.05s
```

This is L1 fixture evidence only. No external provider, SWE-bench, Unity
Editor, or independent-verifier run has been executed.

## Completed RED/GREEN Sequence

1. **Full matrix and honest claim boundary**
   - RED: no runner composed submission, execution, blinding, grading,
     ledger verification, trace closure, and ClaimGate.
   - GREEN:
     `test_hermetic_tracer_closes_the_full_matrix_and_refuses_l2_and_above`
     covers all 12 generic/Unity × Claude/Codex seam × A/B/C coordinates.
2. **Terminal grade authority**
   - RED: trace closure reported `grade_terminal_commit_missing` for every
     cited current grade after terminal-commit authority became mandatory.
   - GREEN: each initial and superseding revision is committed against the
     exact persisted terminal arm state/hash before closure.
   - Negative:
     `test_hermetic_tracer_blocks_when_a_grade_terminal_commit_is_missing`
     proves absent authority blocks publication.
3. **Treatment wire cut**
   - GREEN:
     `test_disabling_supervisor_orchestration_breaks_b_but_not_c` proves the
     B treatment uses the supervisor wire while C remains direct.

## Required Assertions

- Twelve expected execution coordinates and no duplicates.
- One terminal state and one workflow/task/run join per execution.
- Identical runtime, frozen-result, and grade schemas.
- No hidden material or treatment identity before verification.
- Immutable supersession lineage and one exact terminal commit per revision.
- Missing or discrepant terminal authority fails closed.
- Valid ledger chains and a closed objective-to-promotion trace.
- Closed objective-to-grade trace.
- `ClaimGate.max_claim_level(...) == L1`; both L2 and L3 assertions rejected.

## Remaining Operational RED

Add an opt-in external test/run for installed Claude Code and Codex plus
pinned official generic and Unity verifiers. It must use an independently
identified verifier principal before it may seek L2. Runtime, task,
experiment, observability, ledger, grade, trace, replay, and ClaimGate focused
suites must all be green before that external tracer starts.
