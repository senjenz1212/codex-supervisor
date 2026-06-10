# TDD Grill Findings

### Finding 1: Tests Must Use Public Boundaries

Status: resolved

- Risk: helper-only tests miss ledger or artifact mutation behavior.
- Resolution: tests use `State`, proposal APIs, and real temporary files.

### Finding 2: Accepted-With-Gaming Is The Subtle Negative

Status: resolved

- Risk: only testing rejected records misses accepted records with flags.
- Resolution: the no-applyable test includes both rejected and gaming-flagged accepted records.

### Finding 3: Approval Must Verify Bytes

Status: resolved

- Risk: the displayed diff can differ from what approval writes.
- Resolution: approval tests compare target hash to proposal `after_hash`.

### Finding 4: Rollback Must Be Machine-Replayable

Status: resolved

- Risk: rollback depends on human memory.
- Resolution: tests restore through the recorded rollback pointer.

### Finding 5: Existing Report-Only Semantics Must Remain

Status: resolved

- Risk: policy evolution weakens AutoResearch report-only invariants.
- Resolution: regression suite includes existing AutoResearch tests.

## Waivers

- None.
