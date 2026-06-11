# PRD: Policy Overlay Liveness, Regression Verification, And Hygiene

## Problem Statement

The supervisor auto-evolution loop can generate accepted AutoResearch policy ideas, but those ideas are not yet live in subsequent supervised runs. Without a constrained policy surface, applying an idea risks arbitrary prompt or config mutation. Without replayable attribution, trend metrics cannot explain which policy version influenced a run. Without regression detection, a bad overlay can linger until a human notices scattered failures.

## Solution

Introduce one consumed policy overlay at `.supervisor/policy-overlay.yaml`. Gate startup reads this file, renders deterministic advisory guidance into lead instructions, records the overlay hash and proposal id, and passes the same hash into quality trend rows. AutoResearch policy proposals may only target this overlay. Regression verification drafts a rollback proposal when post-overlay metrics degrade, but the proposal remains inert until a human approves it.

## User Stories

- As an operator, I can approve an AutoResearch overlay proposal and see its guidance appear in the next lead instruction with a stable hash.
- As a reviewer, I can trace a quality trend row back to the active overlay hash and proposal id that may have influenced the run.
- As an operator, I can receive a rollback draft when a policy version regresses, while preserving the separate human approval step before any mutation.
- As a maintainer, I can retire noisy lessons and run cadence-based P11 audits without those observations changing gate decisions.

## PRD Promise Contracts

P1. Applied overlay changes lead instructions
- User-visible promise: an operator-approved overlay changes the next composed lead instruction.
- Public boundary: `CodexSupervisorMcpAPI._workflow_gate_start_kwargs`.
- Allowed outcome: the instruction includes deterministic overlay guidance and the gate emits a replayable overlay snapshot event.
- Forbidden outcome: a proposal says it applied but the next instruction is unchanged or lacks an overlay hash.

P2. Only the whitelisted overlay surface is mutable
- User-visible promise: policy evolution cannot write arbitrary prompts, config files, gate predicates, or immutable surfaces.
- Public boundary: `create_policy_evolution_proposals` and `approve_policy_proposal`.
- Allowed outcome: targets are limited to `.supervisor/policy-overlay.yaml`.
- Forbidden outcome: approving an AutoResearch proposal mutates any non-overlay file.

P3. Trend metrics are attributed to policy version
- User-visible promise: quality trend rows explain which overlay hash and proposal id were active.
- Public boundary: `record_quality_trends_for_run` and `query_quality_trends`.
- Allowed outcome: rows persist `policy_overlay_hash` and `policy_proposal_id`.
- Forbidden outcome: trend metrics are detached from the policy version that influenced the run.

P4. Regression produces a draft rollback only
- User-visible promise: after enough runs under a new overlay, metric regression drafts a rollback proposal but never applies it.
- Public boundary: policy regression verification helper.
- Allowed outcome: one regression event and one rollback proposal draft are recorded.
- Forbidden outcome: rollback is applied without operator approval or duplicate drafts are emitted for the same regression window.

P5. Lessons and audits stay fresh
- User-visible promise: repeated lessons fold, unhelpful lessons retire from injection, and sampled P11 audits can run on cadence.
- Public boundary: lesson recording/query helpers and quality trend audit scheduler.
- Allowed outcome: near-duplicate lessons share one row, retired lessons remain in the ledger but are no longer selected, and a due audit writes observational details.
- Forbidden outcome: stale lessons dominate future prompts or audits advance/block gates.

## Implementation Decisions

The overlay is YAML so operators can inspect and review it directly. The overlay is advisory: it changes lead instructions but does not satisfy gates, bypass reviewer panels, or mutate typed outcomes. The overlay block is rendered before lesson guidance so active policy appears before historical reminders. Near-duplicate lessons fold by normalized task class, gate, taxonomy code, root cause, and remediation while preserving an observation count.

## Testing Decisions

Testing must exercise public supervisor boundaries instead of only helper functions. Overlay liveness is tested through gate-start instruction construction and snapshot events. Proposal safety is tested through the AutoResearch policy-evolution API. Trend attribution is tested through persisted rows and read-only summaries. Lesson hygiene and P11 audit cadence are tested with seeded ledgers.

## Out Of Scope

This phase does not change gate predicates, default reviewer policy, fan-out defaults, AutoResearch activation rules, or `DEFAULT_IMMUTABLE_PATHS`. It does not apply regression rollback automatically. It does not adopt a new durable execution runtime or change the two human touchpoints.
