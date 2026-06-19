# Issue Slices: AutoResearch Oracle-Coupling Validity Gate

## Slice 1 - Paired Report Measurement-Validity Metadata

Priority: P1

Scope: Add the report-level and per-arm validity fields to the paired
acceptance pilot output produced by `run_paired_acceptance_pilot`, while
preserving existing calibration metrics and replay artifacts.

PRD promise: P1, P2, P4.

Public boundary: `supervisor.mergeability_bench.run_paired_acceptance_pilot`.

Representative action: Build the mergeability paired report over the existing fixture corpus.

Allowed outcomes: The report includes baseline metrics, an oracle-ceiling arm, per-arm decision-source metadata, report-level applyability fields, and replayable per-task rows.

Forbidden outcomes: The report presents oracle-derived acceptance as independent Supervisor improvement evidence, omits gaming flags, or drops existing calibration artifacts.

Acceptance criteria:

- [ ] `report_label`, `metric_applyable`, `improvement_claim_allowed`, and `gaming_flags` are present in the paired report.
- [ ] The current oracle-derived acceptance path is represented as an `oracle_ceiling` arm with `decision_source=oracle_final_score`.
- [ ] The seeded baseline false accepts remain visible in summary metrics and per-task rows.
- [ ] Exported report artifacts include the same validity metadata as the in-memory return value.

TDD plan: Start with a boundary test that calls `run_paired_acceptance_pilot` and asserts oracle-coupled treatment metadata blocks improvement claims while preserving seeded baseline false accepts.

## Slice 2 - Validity-Focused Mergeability Tests

Priority: P1

Scope: Rewrite the existing paired-pilot assertions so they no longer treat
`supervisor_accept == oracle_accept` as an independent Supervisor win, and add
focused coverage for the validity fields introduced in Slice 1.

PRD promise: P1, P2, P4.

Public boundary: `tests/test_mergeability_bench.py` exercising `run_paired_acceptance_pilot`.

Representative action: Run the existing paired pilot tests against the fixture corpus.

Allowed outcomes: Tests assert the oracle ceiling has zero false accepts only as a ceiling, and they assert `metric_applyable=false` plus `improvement_claim_allowed=false`.

Forbidden outcomes: Tests treat `supervisor_accept == oracle_accept` as a real Supervisor win or assert a negative delta without validity metadata.

Acceptance criteria:

- [ ] The old false-accept reduction assertions are replaced by validity-gate assertions.
- [ ] The oracle ceiling may still report zero false accepts, but only while non-applyable.
- [ ] The test suite proves baseline false accepts are still visible and not hidden by the relabel.
- [ ] The test names match the TDD contract and exercise public report output, not only helper internals.

TDD plan: Replace the previous false-accept reduction assertion with metadata and gaming-flag assertions.

## Slice 3 - Policy Derivation Guard For Oracle-Coupled Reports

Priority: P1

Scope: Extend the AutoResearch policy-derivation guard so oracle-coupled,
non-applyable, or improvement-claim-disabled report records cannot create
policy evolution proposals.

PRD promise: P3.

Public boundary: `supervisor.autoresearch.policy_evolution.derive_policy_evolution_proposals_from_report` and `report_contains_derivable_policy_record`.

Representative action: Present a report or report record containing oracle-coupled treatment evidence to the policy derivation path.

Allowed outcomes: Derivation returns no proposals and records the report as non-derivable.

Forbidden outcomes: A report with `oracle_coupled_treatment_arm`, `metric_applyable=false`, or `improvement_claim_allowed=false` creates an applyable proposal.

Acceptance criteria:

- [ ] `report_contains_derivable_policy_record` rejects oracle-coupled paired-report records.
- [ ] `derive_policy_evolution_proposals_from_report` returns no proposal for non-applyable validity metadata.
- [ ] Existing positive derivation behavior for valid accepted AutoResearch reports remains unchanged.
- [ ] Report-only invariants stay false and no gate authority changes in this slice.

TDD plan: Add or update a policy-derivation regression test using the paired report output or a minimal report record shaped like AutoResearch validation output.

## Coverage Index

P1 is covered by Issue 1 and Issue 2.

P2 is covered by Issue 1 and Issue 2.

P3 is covered by Issue 3.

P4 is covered by Issue 1 and Issue 2.
