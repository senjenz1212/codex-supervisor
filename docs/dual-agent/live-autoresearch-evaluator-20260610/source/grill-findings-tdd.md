# TDD Grill Findings

### Finding 1 - Tests must hit public boundaries

Status: resolved

The test plan could drift into helper-only coverage. The primary tests now call `validate_attempt(...)`, `run_autoresearch_fixture(...)`, and `scripts/run_supervisor_autoresearch.py`, which are the surfaces operators and workflow gates use.

### Finding 2 - Hash mismatch needs pre-execution evidence

Status: resolved

The hash mismatch test uses a marker-writing evaluator and asserts the marker is absent. That proves the script was blocked before execution, not merely rejected after execution.

### Finding 3 - Mutable escape needs checkout protection

Status: resolved

The mutable escape test asserts the attempted outside write appears only as validation evidence and does not write to the source checkout. This covers the operator-facing safety property.

### Finding 4 - Zero variance should be suspicious but not fatal alone

Status: resolved

The test asserts `zero_variance_trials` is present while the validation status can remain accepted if no fatal validation error exists. That preserves deterministic evaluator support.
