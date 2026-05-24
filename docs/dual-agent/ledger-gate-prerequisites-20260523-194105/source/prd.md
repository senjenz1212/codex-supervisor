# PRD: Ledger-Backed Dual-Agent Gate Prerequisites

## Problem

Strict artifact preflight guarantees that PRD, TDD, issue, grill, and implementation-plan files exist, but it does not prove that Claude Code actually reviewed those artifacts before implementation or outcome review.

## Goal

Make the supervisor ledger enforce the dual-agent sequence. Later gates should not run unless earlier review gates have accepted results for the same `run_id` and `task_id`.

## Acceptance Criteria

- `issues_review` is a first-class dual-agent gate.
- `implementation_plan` requires accepted `prd_review`, `issues_review`, and `tdd_review`.
- `execution` requires accepted `implementation_plan`.
- `outcome_review` requires accepted `execution`.
- Missing prerequisites block before Claude Code launches.
- Blocked results record `gate_prerequisites_missing`.
- Artifact exports show required, accepted, and missing prerequisite gates.
- The dual-agent skill explains the enforced sequence.
