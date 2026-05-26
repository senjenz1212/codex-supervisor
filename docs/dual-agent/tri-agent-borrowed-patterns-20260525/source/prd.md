# PRD: Tri-Agent Borrowed Patterns

## Problem

The dual-agent workflow has replay-grade traces, but operators still need task-size routing, severity-aware review decisions, cohort evidence for live probes, stability proposal artifacts, and forward-only compatibility checks.

## Goals

- Route trivial, small, large, and vague work through appropriately deep gate sequences.
- Preserve the existing full lifecycle as the default large route.
- Represent Codex review findings with severity and explicit round-forcing policy.
- Keep receipt replay as the hard truth surface for implementation/test claims.
- Aggregate repeated live probe trials into STABLE, DRIFT-1, or FLIPPING cohorts.
- Generate SP-N stability proposals as review-only artifacts.
- Add forward-only state migrations and replay schema version checks.

## Non-Goals

- No live probe is claimed by this slice.
- No automatic policy or prompt mutation from stability proposals.
- No cost cap is introduced.
- No Telegram workflow-control expansion.

## Acceptance Criteria

- The MCP `run_dual_agent_workflow` tool accepts `task_complexity`.
- A trivial route records `dual_agent_workflow_route`, skips PRD/TDD skill receipt checks, and runs only execution plus outcome review.
- Small and large routes have explicit regression coverage, and route metadata is visible through transcripts.
- Existing default large workflow behavior remains compatible.
- Review packets include `findings` and `round_policy`.
- CRITICAL or IMPORTANT review packet findings force a next round at the workflow boundary.
- Implementation receipts must name all changed files for claimed changes.
- Probe cohort and SP proposal helpers are deterministic and tested, and the cohort wrapper preserves trial-local task/run identity.
- State migrations record applied versions and fail closed on mismatch.
- State migrations fail closed on unknown future migration versions.
- Replay schema version drift is detectable against exported manifest keys without invoking live tools.
