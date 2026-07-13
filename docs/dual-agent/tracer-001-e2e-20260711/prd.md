# PRD: TRACER-001 End-to-End Kernel Thread

## Problem

Passing component tests does not prove that submission, event joins, runtime
execution, task isolation, blinded verification, immutable grading, ledger
provenance, trace closure, and claim authorization compose correctly.

## Goal

Run one pinned generic repository task and one pinned Unity task through A/B/C
on both Claude Code and Codex, producing a complete evidence thread while
`ClaimGate` refuses any claim above L2.

## Promise Contracts

### P1: The matrix is complete

- Two task families × three arms × two runtimes = twelve arm executions.
- Each execution uses the same schemas and its assigned resource ceilings.

### P2: Every lifecycle event joins

- Submission registers workflow, task, assignment, runtime session, and run.
- Normalized events reach one terminal state and join to exactly one workflow.

### P3: Hidden verification remains isolated

- Every arm uses a clean task environment.
- The result is frozen and treatment-blinded before the hidden verifier runs.

### P4: Evidence is immutable and traceable

- Each frozen result receives a verifier-pinned immutable grade.
- Ledger verification passes.
- The trace graph closes from objective through requirement, test, assignment,
  run, artifact, and grade.

### P5: Claims remain honest

- Independent hidden verification may support L2.
- Two tracer tasks cannot support a powered causal, portable, ROI, or
  auto-improvement claim; `ClaimGate` must refuse L3+.

## Non-goals

- Estimating efficacy, discordance, flake rate, or ROI.
- Reusing tracer tasks in PILOT or CONFIRM.
- Substituting fake runtime/verifier tests for the required external thread.
