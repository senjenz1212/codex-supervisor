# TDD Plan: TRACER-001

## Current State

There is no dedicated TRACER-001 end-to-end test or external run artifact in
the current repository snapshot. Component tests are prerequisites, not a
substitute for this plan.

## Public-Boundary RED/GREEN Sequence

1. Add `test_generic_tracer_closes_full_thread_and_caps_claim_at_l2`.
   - First RED: no runner currently composes submission through ClaimGate.
   - Use real local repository materialization and isolated fake provider
     transports only for the deterministic integration test.
2. Add `test_unity_tracer_closes_full_thread_and_caps_claim_at_l2`.
   - Use a pinned Unity fixture for deterministic integration.
3. Add `test_tracer_rejects_missing_or_unjoined_event`.
4. Add `test_tracer_rejects_treatment_identity_in_verifier_packet`.
5. Add `test_tracer_detects_ledger_or_trace_break`.
6. Add an opt-in external test/run for installed Claude Code and Codex plus the
   pinned official generic/Unity verifiers.

## Required Assertions

- Twelve expected execution coordinates and no duplicates.
- One terminal state and one workflow/task/run join per execution.
- Identical runtime, frozen-result, and grade schemas.
- No hidden material or treatment identity before verification.
- Immutable grade and valid ledger chain.
- Closed objective-to-grade trace.
- `ClaimGate.max_claim_level(...) <= L2` and L3 assertion rejected.

## Component Gates

Runtime, task, experiment, observability, ledger, grade, trace, replay, and
ClaimGate focused suites must all be green before the external tracer starts.
