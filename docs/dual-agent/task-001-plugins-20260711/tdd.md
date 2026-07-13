# TDD Plan: TASK-001

## Existing Public-Boundary Tests

1. Generic and Unity materialization/collection return the same frozen schema.
2. Untracked text plus tracked changes produce an applyable patch without
   mutating the workspace/index.
3. Untracked binary creation plus deletion produce an applyable binary patch.
4. Hidden verifier material is absent from the workspace and frozen result.
5. SWE-bench verifier delegates the patch to the official-oracle seam and maps
   its pass statuses to the common grade.

## Remaining Tracer Bullets

1. Reset removes all cross-arm state, including ignored files and process
   artifacts.
2. Malicious symlinks cannot expose hidden verifier roots.
3. Network/image/resource policies are enforced by the runtime environment.
4. Run a pinned official SWE-bench fixture through the unforked harness.
5. Run a pinned Unity project through an installed Unity Test Framework runner.
6. Classify verifier flake and common infrastructure failure separately from a
   treatment failure.

## Verification

```text
.venv/bin/python -m pytest -q tests/test_task_environment.py
```

The first proof for each remaining promise must cross the adapter public
boundary; helper-only Git or path tests are insufficient.
