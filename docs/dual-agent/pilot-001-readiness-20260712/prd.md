# PRD: PILOT-001 Operational Readiness and Pilot

## Problem

Confirmation size, cost, and reliability cannot be chosen honestly without an
operational estimate of B/C discordance, verifier flake, infrastructure
failure, cost, and latency. A pilot becomes invalid if tasks overlap
confirmation, stopping depends on observed discordance, or pilot effects are
reported as causal proof.

## Goal

Run the frozen A/B/C kernel on a disjoint task set solely to estimate operating
rates and derive a frozen confirmation sample-size plan.

## Promise Contracts

### P1: Pilot and confirmation tasks are disjoint

- Pilot task IDs are frozen and hashed before execution.
- No pilot task may appear in confirmation, sealed holdout, or external
  portability strata.

### P2: No optional stopping

- Pilot task count, assignment version, arm ceilings, failure policy, and
  runtime/verifier pins are frozen before the first task.
- The pilot may not run until a desired number of discordant pairs appears.

### P3: The operational kernel is unchanged

- A/B/C meanings, B/C compute matching, assignment, isolation, blinding,
  intention-to-treat, grading, and task-level units match EXP-001.

### P4: Pilot outputs are descriptive

- Estimate discordance, verifier flake, infrastructure failure, cost, and
  latency from one row per unique task.
- Record uncertainty and all failures; do not tune on confirmation tasks.

### P5: Confirmation size is derived and frozen

- Use pilot discordance plus a preregistered alternative and exact two-sided
  McNemar power at alpha 0.05 and power 0.90.
- Validated discordant-pair table: 263 at 60%, 114 at 65%, 65 at 70%, and 42
  at 75% B-win among discordant pairs.

### P6: Claims remain below causal

- PILOT estimates feasibility; it does not authorize “supervisor improves
  outcomes.”
- ClaimGate must remain below L3.

## Non-goals

- Selecting a favorable effect after observing pilot outcomes.
- Confirming efficacy, portability, ROI, or auto-improvement.
- Fixing pilot task count or arm ceilings in this document without an approved
  preregistration and budget.
