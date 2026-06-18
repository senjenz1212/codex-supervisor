# TDD Grill Findings

## Finding 1: First Tests Must Exercise The Public Boundary

The TDD plan avoids helper-only parsing by naming manifest, calibration, and paired pilot public functions as the first RED boundaries. Fixture helper tests may follow, but they cannot replace the full corpus loading and reporting path.

Resolution: Incorporated into MBP-1, MBP-2, and MBP-3.

## Finding 2: The Pilot Must Prove Both Arms Share A Candidate Pool

A baseline false accept is meaningless if the Supervisor arm sees a different candidate. The TDD plan now includes an explicit same-pool assertion and disagreement row test.

Resolution: Incorporated into MBP-2.

## Finding 3: Report-Only Must Be Tested As A Safety Property

The TDD plan now names report-only invariant checks as first-class tests rather than prose. The pilot may produce evidence, but it must not mutate policy, advance gates, or create an applyable policy claim.

Resolution: Incorporated into MBP-3.
