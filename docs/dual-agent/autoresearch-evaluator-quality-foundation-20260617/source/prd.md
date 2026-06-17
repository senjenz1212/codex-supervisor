# PRD: AutoResearch Evaluator Quality Foundation

Task id: `autoresearch-evaluator-quality-foundation-20260617`

## Problem Statement

Supervisor AutoResearch can now execute hash-pinned evaluators and emit report-only evidence, but the default replay-corpus evaluator can return saturated `1.0, 1.0, 1.0` trial values without proving that a candidate changed supervisor behavior. Those identical values correctly produce `zero_variance_trials`, and policy derivation then refuses the record because any gaming flag makes the record non-applyable. The program needs a measurement floor that proves evaluator quality before research squads, bounded edits, or policy proposals rely on the metric.

The failure mode is subtle: an evaluator may have real `evaluator_execution` provenance and still be too weak to distinguish a no-op candidate from a harmful change or a known-good repair. If AutoResearch trusts that metric, the loop can optimize saturated fixtures rather than supervisor outcomes. The next production slice must therefore make evaluator quality, candidate sensitivity, and held-out control behavior first-class evidence.

## Solution

Add an evaluator-quality control layer around AutoResearch evaluator execution and policy proposal derivation. The layer runs a held-out control suite containing a no-op candidate, a harmful or irrelevant candidate, and a known-good seeded candidate. It records supervisor-generated control receipts and ledger events, verifies deterministic evaluator behavior by repeated same-input execution and output hash comparison, and requires control-validated positive delta before a report can be considered derivable.

The implementation will keep the current report-only authority model. AutoResearch may emit validation records, evaluator-quality receipts, and draft policy proposals only after the control suite proves that the evaluator can discriminate candidate behavior. It will not auto-apply changes, mutate policy, advance gates, or replace the reviewer panel. The saturated replay result remains useful as negative evidence: identical all-pass trials without control-validated delta stay blocked from proposal derivation.

## User Stories

1. As a supervisor maintainer, I can run AutoResearch and see whether the evaluator distinguished no-op, bad, and known-good candidates before trusting the candidate metric.
2. As an operator, I can inspect ledger-backed evaluator-quality receipts that show control candidate refs, hashes, deltas, and pass or fail decisions.
3. As a reviewer, I can reject a policy proposal if the report was produced by a saturated replay corpus or by self-declared evaluator metadata.
4. As a future AutoResearch author, I can add better candidate generators only after the held-out measurement floor proves it can compare candidates.
5. As a gate owner, I can trust that evaluator-quality checks are evidence only and cannot advance workflow gates or apply policy changes.

## PRD Promise Contracts

P1. AutoResearch evaluator quality controls distinguish no-op, bad, and known-good candidates before proposal derivation.

- User-visible promise: Proposal derivation has access to supervisor-generated evidence proving that the evaluator ranked control candidates correctly.
- Representative action: Run an AutoResearch report through validation and policy derivation with control results attached.
- Public boundary: `supervisor.autoresearch.policy_evolution.derive_policy_evolution_proposals_from_report`.
- Allowed outcomes: no-op has no positive delta, harmful or irrelevant candidate regresses or fails, known-good improves, and only the known-good pattern can proceed.
- Forbidden outcomes: no-op or harmful controls create applyable proposals, or known-good control evidence is omitted while proposal derivation still succeeds.
- Related user stories: 1, 2, 3.

P2. Zero-variance saturated results cannot create applyable policy proposals without control-validated candidate-sensitive delta.

- User-visible promise: `zero_variance_trials` remains a gaming flag, and saturated all-pass replay results stay blocked unless separate controls prove candidate sensitivity.
- Representative action: Validate a report with `metric_trials=[1.0, 1.0, 1.0]`, `metric_source=evaluator_execution`, and no passing control suite.
- Public boundary: `supervisor.autoresearch.validation.validate_attempt` and policy derivation.
- Allowed outcomes: validation may be accepted as report-only evidence, but derivation emits no applyable proposal and records the reason.
- Forbidden outcomes: identical all-pass trials alone produce a policy proposal or clear a gaming flag by self-declared determinism.
- Related user stories: 1, 3.

P3. Human approval and report-only invariants remain unchanged for every AutoResearch output.

- User-visible promise: Evaluator-quality checks add evidence but do not grant mutation authority or bypass human approval.
- Representative action: Inspect a successful known-good control run and its derived proposal.
- Public boundary: `supervisor.autoresearch.report`, `supervisor.autoresearch.policy_evolution`, and the existing approval path.
- Allowed outcomes: reports and proposals keep `default_change_allowed=false`, `policy_mutated=false`, `gate_advanced=false`, and operator approval is still required.
- Forbidden outcomes: evaluator-quality success applies a policy overlay, advances a workflow gate, or marks reviewer panel evidence unnecessary.
- Related user stories: 2, 5.

P4. Deterministic evaluator claims are verified by execution rather than trusted as metadata.

- User-visible promise: A caller cannot unlock proposal derivation by stamping an evaluator or report as deterministic.
- Representative action: Attach deterministic metadata without repeated same-input execution evidence, then attempt derivation.
- Public boundary: evaluator execution result validation and report validation.
- Allowed outcomes: verified same-input output hashes can support deterministic classification; unverified metadata is ignored or rejected.
- Forbidden outcomes: self-declared determinism suppresses `zero_variance_trials` or bypasses held-out controls.
- Related user stories: 2, 3.

## Implementation Decisions

- Add evaluator-quality dataclasses or pure helpers under `supervisor/autoresearch/` rather than embedding control logic in the CLI.
- Store control results in report records using stable fields for control name, candidate ref, candidate hash, metric before, metric after, metric delta, evaluator run refs, and verdict.
- Treat the held-out control corpus as immutable evaluator input. It must be hash-pinned and excluded from candidate mutable paths.
- Verify deterministic behavior by running the same evaluator input at least twice and comparing normalized output hashes, not by trusting caller-supplied metadata.
- Keep policy derivation conservative: any missing control evidence, saturated all-pass result, candidate path mismatch, or gaming flag blocks applyable proposal derivation.
- Emit additive ledger events such as `autoresearch_evaluator_quality_control_started` and `autoresearch_evaluator_quality_control_completed`; do not change gate tables.
- Keep the replay-corpus evaluator available, but do not let its all-pass saturated result satisfy the candidate-sensitive floor by itself.

## Testing Decisions

- The first RED test targets the policy proposal public boundary and proves a no-op control cannot create an applyable proposal.
- Public-boundary tests must exercise report validation, evaluator execution artifacts, and policy derivation rather than helper-only functions.
- Use fixture evaluator scripts and fixture candidate artifacts so tests stay deterministic and do not call live model providers.
- Include a saturated replay fixture matching the observed `1.0, 1.0, 1.0` failure so the regression remains visible.
- Include deterministic verification tests that compare repeated output hashes and reject self-declared metadata without evidence.
- Re-run existing AutoResearch, policy evolution, workflow-driver, and planning-validator tests touched by the slice.

## Out of Scope

- Do not add research squads, multi-agent candidate generation, or SkillOpt-style edit search in this slice.
- Do not auto-apply policy overlays, mutate production prompts, advance gates, or change reviewer-panel authority.
- Do not replace the existing replay-corpus evaluator; constrain how its evidence can be used.
- Do not add live provider calls, network access, or external benchmark dependencies to default tests.
- Do not change the Postgres ledger lane, AXI orchestration semantics, Cursor defaults, or runtime-evidence trust boundary except where required to record evaluator-quality receipts.
