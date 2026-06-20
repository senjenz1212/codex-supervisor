## Slice 1: Enforce Held-Out Mergeability Corpus Controls

Priority: high

Scope: Build manifest validation that rejects any held-out task lacking a positive control, negative control, or public-pass hidden-fail trap. Persist split metadata in the manifest and report so held-out rows are replayable and not silently mixed with in-sample rows.

PRD promise: P1, P2, P7

Public boundary: `validate_mergeability_corpus`

Acceptance criteria:
- [ ] Missing positive control is rejected with a task-specific error.
- [ ] Missing negative control is rejected with a task-specific error.
- [ ] Missing public-pass hidden-fail trap is rejected with a task-specific error.
- [ ] Held-out split metadata remains present in manifest and exported artifacts.

## Slice 2: Report Honest Held-Out Metrics And No-Regression Semantics

Priority: high

Scope: Extend paired reports with held-out/dev separation, sample-size denominators, approximate confidence intervals, best-of-K guardrails, and no-regression findings that protect only oracle-positive prior true accepts rejected by the supervisor arm.

PRD promise: P3, P4, P5, P6, P7

Public boundary: `run_paired_acceptance_pilot`

Acceptance criteria:
- [ ] Prior oracle-negative false accepts may be rejected without a no-regression finding.
- [ ] Prior oracle-positive accepted cases rejected by the full gate are reported as no-regression findings.
- [ ] Each arm exposes `n_bad`, `n_good`, false-accept and true-accept interval fields.
- [ ] Best-of-K in-sample metrics cannot be labeled as held-out improvement.
- [ ] Report-only invariants stay false and policy derivation returns no proposals.
