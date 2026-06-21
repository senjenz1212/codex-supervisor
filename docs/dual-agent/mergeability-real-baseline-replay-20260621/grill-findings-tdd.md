# TDD Grill Findings

### Finding 1

status: resolved

The tests could have targeted a helper that normalizes baseline rows. The TDD plan now starts at `run_powered_factorial_mergeability_evaluation`, the public boundary that produces the arm comparison operators inspect.

### Finding 2

status: resolved

The first RED could be ambiguous if it only checks an internal default. The plan now asserts observable report behavior: absent baseline decisions must not count as accepted outcomes in the powered report.

### Finding 3

status: resolved

Replay evidence could be asserted by field presence only. The plan now includes a hash mismatch case to prove the report validates the candidate artifact identity rather than merely copying metadata.

### Finding 4

status: resolved

Legacy fixture calibration could be accidentally broken by the real-baseline requirement. The plan now includes a compatibility test that preserves calibration while labeling metadata baseline evidence honestly.
