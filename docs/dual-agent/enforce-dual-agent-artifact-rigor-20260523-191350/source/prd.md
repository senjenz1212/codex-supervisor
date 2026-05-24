# PRD: Enforce Dual-Agent Artifact Rigor

## Problem

Dual-agent gates can be invoked without durable PRD, TDD, grill, issue, or visual-review artifacts. That makes a run difficult to audit and lets implementation or outcome-review gates proceed on incomplete context.

## Goal

Every implementation-plan, execution, and outcome-review gate should enforce strict artifact preflight by default. The gate should block before launching Claude Code when required artifacts are missing, record the blocked result in the supervisor ledger, and still export readable Markdown artifacts for the operator.

## Acceptance Criteria

- `start_dual_agent_gate` defaults to strict artifact policy.
- Strict implementation/review gates require PRD, TDD plan, grill findings, issues, and implementation-plan artifacts as applicable.
- User-facing outcome-review gates require screenshots.
- Accepted and blocked gate results include `artifact_rigor` details.
- Gate calls auto-export readable artifacts under `docs/dual-agent/<task_id>/`.
- Skill docs instruct Codex to pass strict artifact parameters and verify the artifact export.
