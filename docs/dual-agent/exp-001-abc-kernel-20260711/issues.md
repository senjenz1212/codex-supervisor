# Issues: EXP-001

## E1: Preregistered arm and assignment contract

- PRD promises: P1, P2
- Public boundary: `ExperimentSpec`, `ExperimentKernel.assign`
- Blocked by: TASK-001
- Acceptance:
  - B and C ceilings must match.
  - A/B/C canonical treatment hashes must all differ.
  - Treatment hashes are persisted in preregistration and assignment, and
    descriptor mutation after assignment is rejected.
  - Assignment is HMAC-derived, block-aware, persisted before execution, and
    immutable for the task/version.
  - All six orders are reachable.

## E2: One task-level outcome per arm

- PRD promises: P3, P6
- Public boundary: `ExperimentKernel.run_task`
- Blocked by: E1
- Acceptance:
  - Exactly A/B/C outcomes are returned once per unique task.
  - Retries remain internal to the arm.
  - Treatment failures remain in the denominator with zero score.

## E3: Isolation and blinded verification

- PRD promises: P4, P5
- Public boundary: arm executor plus `blind_frozen_result`
- Blocked by: RUNTIME-001, TASK-001, E1
- Acceptance:
  - Clean runtime/task state is created per arm.
  - B/C compute/resource hashes match independently of distinct treatments.
  - Launch metadata and execution receipts bind the preregistered treatment
    hash and reject mismatches.
  - No arm identifier survives anywhere in the verifier envelope.
  - Grade-to-arm join occurs after verifier return.

## E4: Reviewer sequencing

- PRD promises: P7
- Public boundary: experiment review coordinator
- Blocked by: E2
- Acceptance:
  - Primary packet excludes the lead outcome.
  - Adjudicator packet requires frozen primary reviews and includes the lead
    outcome only afterward.

## E5: Paired analysis handoff

- PRD promises: P1, P3, P6
- Public boundary: `analyze_paired_outcomes`
- Blocked by: E2
- Acceptance:
  - Emit one task row with A/B/C pass, cost, latency, flake, and infra fields.
  - B/C analysis reports n11/n10/n01/n00, exact McNemar, and paired Newcombe CI.
