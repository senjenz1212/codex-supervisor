# Grill Findings

## Finding 1: Artifact Existence Does Not Prove Review

The previous strict artifact slice could still let Codex jump directly to implementation as long as files existed.

Resolution: strict preflight now checks accepted gate results in the supervisor ledger.

## Finding 2: Issue Slicing Was Implicit

Without an `issues_review` gate, issue slicing could be hidden inside PRD or TDD review and not independently auditable.

Resolution: `issues_review` is now a first-class gate and a prerequisite for TDD and implementation-plan gates.

## Finding 3: Blocks Need Specific Reasons

Operators need to distinguish missing files from missing accepted gates.

Resolution: blocked preflight uses `gate_prerequisites_missing` when the ledger sequence is incomplete and exports prerequisite details.
