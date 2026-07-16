# Implementation Plan: TASK-001

## Scope Note

Planning only; no task or verifier source/test is edited in this task.

## Sequence

1. Validate all TaskSpec pins before creating a workspace.
2. Materialize each arm into a unique detached checkout.
3. Collect a patch with an isolated temporary Git index/object directory so
   untracked and binary files are included without mutating agent state.
4. Freeze and hash the result before invoking any verifier.
5. Keep hidden roots and verifier credentials outside the public workspace and
   runtime environment.
6. Delegate generic and Unity verification to pinned adapters and normalize the
   resulting Grade.
7. Enforce image/network/resource policy in the runtime/task composition layer.
8. Add cleanup/recovery for crashes and prove no workspace reuse across arms.

## Integration Gates

- RUNTIME-001 supplies the run result hash and isolated process lifecycle.
- EXP-001 creates one clean environment per arm and joins grades only after
  blinding.
- GRADE-001 appends immutable verifier revisions.
- TRACER-001 must exercise one generic and one Unity task before pilot.

## Stop Conditions

Do not call a task plugin complete from callback-only verifier tests. A live
official-harness/Unity receipt is separate evidence and must be reported as
not run until it exists.
