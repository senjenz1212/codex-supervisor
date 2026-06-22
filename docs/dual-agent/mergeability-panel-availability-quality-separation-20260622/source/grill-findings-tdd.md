## Findings

### Finding 1

Status: resolved

Concern: The TDD plan must start at the mergeability report boundary, not at helper-only functions.

Resolution: The first RED tests exercise public mergeability reporting and full-gate decision behavior before helper-level checks.

### Finding 2

Status: resolved

Concern: Production blocking and measurement labeling need to be tested together so the gate is not weakened.

Resolution: The first test checks missing-verdict production blocking while asserting the measurement row receives panel_missing_verdict_block rather than panel_quality_reject.

### Finding 3

Status: resolved

Concern: Metric computation can overclaim when no fully available roster rows exist.

Resolution: The TDD plan includes an explicit zero-denominator refusal test for full-roster availability.

### Finding 4

Status: resolved

Concern: Reviewer diagnostics can become an oracle leak if failure metadata includes hidden fields.

Resolution: Diagnostics tests include oracle isolation so failure metadata does not expose hidden SWE-bench or mergeability oracle material.
