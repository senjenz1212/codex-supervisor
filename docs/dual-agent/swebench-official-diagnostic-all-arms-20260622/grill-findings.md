### Finding 1

Status: resolved

Question: Can one oracle-positive row satisfy the matched true-accept promise?

Resolution: No. The existing matched-TAR helper requires at least two oracle-positive candidates, so the diagnostic success path uses two oracle-good rows and one oracle-bad row. The PRD and TDD plan now name that denominator floor explicitly.

### Finding 2

Status: resolved

Question: Is exposing only the nested replay report enough for hidden oracle isolation evidence?

Resolution: No. Operators need the all-arms report to surface hidden_field_leak_check directly, while still preserving the nested official replay report for audit. The wrapper carries the leak check at top level.

### Finding 3

Status: resolved

Question: Could an unavailable reviewer panel or baseline still leave an impressive FAR number in the report?

Resolution: The diagnostic marks the report unavailable when baseline or S_full evidence is unavailable, records unavailable reasons, and keeps claim flags false. Tests assert those blocked outcomes through the runner boundary.
