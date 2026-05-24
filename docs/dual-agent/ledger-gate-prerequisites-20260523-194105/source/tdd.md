# TDD Plan: Ledger-Backed Gate Prerequisites

## Public Boundary

The public boundary is the Codex-facing MCP tool `start_dual_agent_gate`.

## Tests

- `implementation_plan` with planning artifacts but no accepted PRD/issues/TDD gates blocks and does not call Claude.
- `implementation_plan` runs after accepted PRD/issues/TDD gates exist.
- `issues_review` runs after PRD review is accepted.
- `execution` blocks until `implementation_plan` is accepted.
- `outcome_review` blocks until `execution` is accepted.
- Exported artifact-rigor Markdown includes required, accepted, and missing prerequisite gates.
- Skill docs mention the enforced ledger sequence and `gate_prerequisites_missing`.

## Verification

- Focused MCP/artifact/doc tests.
- Full pytest suite.
- Live dual-agent gate chain using the actual Claude Code `/lead` boundary.
