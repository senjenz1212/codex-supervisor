# TDD Grill Findings

### Finding 1: The First Tests Hit The Public Report Boundary

Status: resolved

The TDD plan starts at `run_paired_acceptance_pilot`, which is the same public boundary operators and downstream tooling consume. Helper-level metadata functions may be added later, but the first proof must exercise the exported report shape.

Resolution: Keep OCV-1, OCV-2, and OCV-3 at the paired-report boundary.

### Finding 2: Policy Derivation Needs A Separate Boundary Test

Status: resolved

Report metadata alone would not prevent a policy proposal if derivation ignores the fields. The derivation path needs a direct regression test because it is the public boundary that creates applyable proposal objects.

Resolution: OCV-4 directly exercises policy derivation and non-derivable report detection.

### Finding 3: Avoid Replacing The Real Slice With A Test Name Rename

Status: resolved

The previous `test_paired_acceptance_report_cannot_create_applyable_policy_claim` reused an AutoResearch helper and did not prove the paired report itself was rejected. The new plan must ensure the paired report or a paired-report-shaped record reaches the derivation guard.

Resolution: OCV-4 names the paired report oracle-coupling condition explicitly and requires no proposal.

### Finding 4: Report-Only Invariants Remain Required

Status: resolved

The new validity fields should tighten evidence claims without granting policy or gate authority. Tests must keep `default_change_allowed`, `policy_mutated`, and `gate_advanced` false.

Resolution: OCV-5 preserves the existing report-only invariant checks as regression coverage.
