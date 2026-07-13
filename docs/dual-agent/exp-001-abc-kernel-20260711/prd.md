# PRD: EXP-001 A/B/C Efficacy Kernel

## Problem

Comparing a supervisor arm only with a lower-compute baseline confounds harness
organization with extra tokens, retries, and review. Attempt-level counting,
shared state, unblinded grading, or post-hoc assignment would further invalidate
the result.

## Goal

Run a preregistered, task-paired A/B/C experiment where B-vs-C is primary,
assignments are immutable before execution, arm environments are isolated,
grading is blinded, and every unique task contributes one outcome per arm.

## Promise Contracts

### P1: Arms and estimand are fixed

- A: production baseline.
- B: supervisor.
- C: compute-matched direct with B's ex-ante ceilings and no supervisor
  structure.
- Each arm is preregistered with a canonical `TreatmentDescriptor` binding its
  adapter/entrypoint, instruction template, and treatment configuration.
- A/B/C treatment hashes must all differ; an arm label alone is not part of
  the treatment hash and cannot make identical execution count as distinct.
- Primary comparison: B vs C. B vs A and C vs A are secondary.

### P2: Assignment is deterministic and persisted first

- Public boundary: `ExperimentKernel.assign`.
- One HMAC-derived order from the six A/B/C permutations is persisted before
  any arm starts and remains sticky across retries.
- The immutable preregistration and assignment both persist the three
  treatment hashes. Changing a descriptor after assignment is a hard error.

### P3: The unit is one unique task

- Public boundary: `ExperimentKernel.run_task`.
- Retries, subagents, and reviewers stay inside an arm; the result contains
  exactly one final outcome per arm.

### P4: Arms are isolated and compute matched

- B and C budgets are identical before execution.
- B and C independently require the same compute/resource hash even though
  their treatment hashes differ.
- Every arm gets a clean workspace/container/session with no shared lessons,
  memory, caches, or treatment artifacts.
- Launch metadata and the execution receipt bind the selected treatment hash
  and full plan fingerprint; the kernel rejects any mismatch.

### P5: Verification is blinded

- The arm result is frozen and hashed before verification.
- All direct and nested arm identity is removed from the verifier packet.
- Arm identity is joined to the grade only after verification.

### P6: Failures follow intention-to-treat

- Treatment crash, timeout, empty or inapplicable patch, over-budget, and
  treatment-specific provider failure score as failure.
- A common pre-treatment infrastructure failure may rerun the whole block once,
  never one favored arm.

### P7: Review timing is enforced

- Primary reviewers receive task/diff/evidence without the lead outcome.
- Outcome-aware adjudication occurs only after primary reviews are frozen.

## Non-goals

- Reusing mergeability accept/reject arms as efficacy outcomes.
- Choosing pilot or confirmation sample sizes before pilot evidence.
- Claiming causal improvement from kernel unit tests or tracer tasks.
