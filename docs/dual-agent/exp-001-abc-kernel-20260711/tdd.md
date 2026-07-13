# TDD Plan: EXP-001

## Existing Public-Boundary Tests

1. `test_assignment_is_deterministic_and_persisted_before_execution`
2. `test_verifier_is_blinded_and_arm_identity_is_joined_after_grading`
3. `test_crashed_arm_is_scored_as_an_intention_to_treat_failure`
4. `test_reviewer_packets_blind_primary_judges_but_not_late_adjudication`
5. `tests/test_efficacy_analysis.py` covers paired task tables, exact power
   values, pilot-derived confirmation sizing, disjoint task sets, and B-vs-C
   ROI.

## Implemented RED/GREEN Tracers

1. Reject duplicate treatment hashes and post-assignment descriptor mutation.
2. Reject receipts whose treatment hash differs from preregistration or the
   launched plan; independently reject mismatched B/C compute/resource hashes.
3. Wire A/B/C through production-baseline, supervisor-orchestration, and
   compute-matched-direct adapters. Cutting the supervisor wire must break B
   without breaking C.
4. Reject a nested or output-level arm identifier in the verifier envelope.
5. Prove B and C use clean, non-overlapping workspaces, sessions, caches, and
   lesson namespaces.
6. Prove a retry reuses its persisted arm and cannot change assignment version.
7. Enforce one whole-block retry for a classified common pre-treatment outage;
   forbid per-arm selective rerun.
8. Classify timeout, empty patch, apply failure, and over-budget as ITT failures.
9. Require stored primary-review hashes before constructing adjudication.
10. Integrate real `AgentRuntime`, `TaskEnvironmentAdapter`, verifier, ledger,
   grade revision, trace graph, and ClaimGate in TRACER-001.

## Verification

```text
.venv/bin/python -m pytest -q \
  tests/test_experiment_kernel.py \
  tests/test_arm_executor.py \
  tests/test_tracer_001_e2e.py
```

Unit-test fake executors and the hermetic tracer remain fixture/L1 evidence;
they must not be described as operational efficacy or isolated real pilot runs.
