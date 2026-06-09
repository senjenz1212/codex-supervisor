# PRD Grill Findings: no-mistakes External Validator

## Findings

### Finding G1

status: resolved
severity: high
question: Could no-mistakes become a second source of truth?
resolution: The PRD requires no-mistakes to emit ledger evidence only. Existing
gates remain authoritative, and required/shipping mode can only block a new
post-acceptance validation result without rewriting prior typed gate decisions.

### Finding G2

status: resolved
severity: high
question: Could post-acceptance auto-fixes make the accepted outcome stale?
resolution: P4 requires clean-branch validation in a temporary detached Git
worktree, changed-file and HEAD snapshots, and a `changed_requires_rerun`
verdict when validation mutates files or commits.

### Finding G3

status: resolved
severity: medium
question: Could local developer machines fail because no-mistakes is missing?
resolution: P2 keeps the default policy off. Advisory mode records
unavailable/skipped evidence, while required and shipping modes block with an
actionable preflight reason.

## Decision

No open findings remain. The slice proceeds only if tests prove safe defaults,
isolated validation, post-acceptance ordering, and gate-authority preservation.
