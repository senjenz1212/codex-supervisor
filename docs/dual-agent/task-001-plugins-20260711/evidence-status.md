# Evidence Status: TASK-001

## Status

**Core adapters locally tested; operational plugins not yet proven.**

## Current Repository Evidence

`tests/test_task_environment.py` exercises:

- identical Generic/Unity frozen-result shapes;
- applyable untracked text and binary patches without index mutation;
- hidden-root exclusion before freeze; and
- delegation to the official SWE-bench oracle seam.

Focused command:

```text
.venv/bin/python -m pytest -q tests/test_task_environment.py
```

Observed result: **6 passed**. This pack does not infer live verifier success
from unit tests.

## Not Yet Proven

- No real SWE-bench official container/harness run was executed here.
- No Unity Editor/Test Framework run was executed here.
- The Unity adapter currently proves project identity and callback isolation,
  not full Unity execution.
- Recorded image, OS, architecture, network, and resource pins are not shown by
  these tests to be enforced at runtime.
- No cross-arm cleanup stress test, symlink leak test, external review, skill
  receipt, or commit was produced.

## Claim Boundary

Local tests support adapter schemas, patch completeness, and basic hidden-test
separation. They do not prove official external grading, Unity compatibility,
or end-to-end task-family parity.
