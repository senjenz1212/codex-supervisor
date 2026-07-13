# Evidence Status: TASK-001

## Status

**Core adapters locally tested; one Unity EditMode compatibility run recorded;
official SWE-bench execution remains unproven.**

## Current Repository Evidence

`tests/test_task_environment.py` exercises:

- identical Generic/Unity frozen-result shapes;
- applyable untracked text and binary patches without index mutation;
- hidden-root exclusion before freeze; and
- delegation to the official SWE-bench oracle seam.

The sanitized receipt
`compatibility-unity-6000.3.10f1-20260713.json` records one successful Unity
6000.3.10f1 EditMode execution through the Unity Test Framework verifier seam.
It pins the Unity executable, project revision, result, patch, hidden tree, and
frozen-result hashes.

Focused command:

```text
.venv/bin/python -m pytest -q tests/test_task_environment.py
```

Observed result: **6 passed**. This pack does not infer live verifier success
from unit tests.

## Not Yet Proven

- No real SWE-bench official container/harness run was executed here.
- One Unity Editor/Test Framework compatibility run exists, but it is a
  one-test smoke and not a representative Unity benchmark.
- Recorded image, OS, architecture, network, and resource pins are not shown by
  these tests to be enforced at runtime.
- No powered cross-runtime or cross-task-family efficacy result exists.

## Claim Boundary

Local tests support adapter schemas, patch completeness, and basic hidden-test
separation; the Unity receipt adds narrow operational compatibility. They do
not prove official SWE-bench grading, task-family parity, or outcome
improvement.
