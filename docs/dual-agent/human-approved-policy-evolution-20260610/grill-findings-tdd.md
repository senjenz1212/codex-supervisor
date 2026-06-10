# TDD Grill Findings: Human-Approved AutoResearch Policy Evolution

### Finding 1: First Tests Must Hit Public Boundaries

Status: resolved

- Risk: helper-only tests could miss ledger payload or file mutation behavior.
- Resolution: every primary test uses `State`, a temporary repo root, and the exported policy-evolution functions rather than private helpers.

### Finding 2: Test Accepted-With-Gaming Separately From Rejected

Status: resolved

- Risk: `validation_status=accepted` with `gaming_flags` is the subtle case.
- Resolution: the TDD plan includes both rejected and accepted-with-gaming records in the no-applyable-proposal test.

### Finding 3: Approval Must Prove Exact Bytes

Status: resolved

- Risk: a diff can be displayed while approval writes different content.
- Resolution: the approval test compares the applied target hash to the proposal's recorded `after_hash`.

### Finding 4: Denial And Rollback Need Ledger Evidence

Status: resolved

- Risk: denial and rollback can be treated as out-of-band operator actions.
- Resolution: tests assert denial and rollback events in the supervisor ledger.

### Finding 5: Preserve Existing Report-Only Invariants

Status: resolved

- Risk: adding policy evolution could accidentally mutate AutoResearch report semantics.
- Resolution: regression commands include existing AutoResearch tests, especially `default_change_allowed=false` checks.

## Waivers

- None.
