# TDD Plan: PILOT-001

## Current Mathematical Coverage

`tests/test_efficacy_analysis.py` currently checks:

- exact discordant-pair requirements 263/114/65/42;
- task-level n11/n10/n01/n00 analysis with exact McNemar and paired Newcombe CI;
- confirmation sizing from a synthetic 20% pilot discordance rate;
- pilot/confirmation task-set disjointness; and
- ROI based on B minus compute-matched C.

These tests do not constitute a pilot.

## Remaining Public-Boundary Tracer Bullets

1. Readiness validator rejects a missing TRACER, red component suite, unpinned
   runtime/verifier, absent budget, or dirty/uncommitted execution manifest.
2. Pilot manifest rejects duplicate task IDs, confirmation overlap, mutable
   task count, unequal B/C ceilings, and an outcome-dependent stop rule.
3. Pilot estimator rejects attempt-level rows and requires exactly one unique
   task row.
4. Pilot runner retains treatment failures and permits only one whole-block
   rerun for a classified common pre-treatment outage.
5. Readout records uncertainty, task-set hash, manifest hash, all arm costs and
   latencies, and ClaimGate below L3.
6. Confirmation-plan artifact is immutable and derived only after the frozen
   pilot readout.

## Verification Gates

Unit and integration tests precede an opt-in external pilot command. The
external run must require explicit roster, manifest, budget, and operator
authorization; it must never be triggered by the normal unit suite.
