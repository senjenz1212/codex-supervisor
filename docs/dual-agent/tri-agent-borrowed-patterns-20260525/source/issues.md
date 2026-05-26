# Issues: Tri-Agent Borrowed Patterns

## DA-BORROW-001: Task-Complexity Routing

Add deterministic route selection for trivial, small, large, and vague workflow tasks. Route output must be recorded in the supervisor ledger.

## DA-BORROW-002: Severity-Aware Review Packet

Extend Codex review packets with findings, severity, receipt replay evidence, and a round-forcing policy.

## DA-BORROW-003: Receipt Replay Tightening

Require implementation receipts to explicitly cover every changed file and reject vague substring claim matches.

## DA-BORROW-004: Probe Cohorts

Add deterministic cohort aggregation for repeated probe trials, including status counts, MAST counts, cost/tokens, and worst-case observed metrics.

## DA-BORROW-005: SP-N Stability Proposals

Generate review-only stability proposals from cohort summaries without mutating runtime policy.

## DA-BORROW-006: Forward Migrations and Version Drift

Add a forward-only state migration registry and replay schema compatibility checks.
