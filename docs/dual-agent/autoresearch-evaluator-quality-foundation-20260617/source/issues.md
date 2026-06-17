# Issues: AutoResearch Evaluator Quality Foundation

## Slice 1: Evaluator Quality Control Schema and Report Evidence

Priority: P1
Estimate: medium

Scope: Add stable evaluator-quality control payloads to AutoResearch reports and validation records. The schema must represent no-op, harmful, known-good, and deterministic verification controls with candidate refs, artifact hashes, metric deltas, evaluator run refs, verdicts, and reasons.

PRD promise: P1, P2, P3, P4.

Public boundary for first RED test: `supervisor.autoresearch.validation.validate_attempt`.

Acceptance criteria:

- [ ] Control results serialize into report records with stable hashes and candidate refs.
- [ ] Missing required controls block proposal derivation and produce an auditable reason.
- [ ] Existing report-only invariants remain false for policy mutation and gate advancement.
- [ ] Fixture reports without evaluator-quality controls remain readable but non-applyable.

## Slice 2: Held-Out Control Corpus and Candidate Fixtures

Priority: P1
Estimate: medium

Scope: Add a small immutable held-out corpus and fixture candidate artifacts that exercise no-op, harmful or irrelevant, and known-good behavior. The corpus must be hash-pinned and excluded from mutable candidate paths.

PRD promise: P1, P2.

Public boundary for first RED test: evaluator execution against fixture attempt worktrees.

Acceptance criteria:

- [ ] No-op candidate produces no positive improvement.
- [ ] Harmful or irrelevant candidate fails or regresses.
- [ ] Known-good seeded candidate produces positive delta.
- [ ] Corpus files are hash-pinned and cannot be listed as mutable candidate paths.

## Slice 3: Determinism Verification and Saturation Guard

Priority: P1
Estimate: small

Scope: Verify deterministic evaluator behavior by repeated same-input execution and normalized output hash comparison. Preserve `zero_variance_trials` as a gaming flag and prevent saturated all-pass reports from creating proposals without passing controls.

PRD promise: P2, P4.

Public boundary for first RED test: policy derivation from a saturated report record.

Acceptance criteria:

- [ ] Saturated `1.0, 1.0, 1.0` evaluator-execution reports produce no applyable proposal.
- [ ] Self-declared deterministic metadata without execution evidence is ignored or rejected.
- [ ] Verified same-input output hashes are recorded as evidence but do not alone prove improvement.
- [ ] Control-validated positive delta is required before proposal derivation succeeds.

## Slice 4: Policy Derivation Gate Integration

Priority: P1
Estimate: medium

Scope: Integrate evaluator-quality checks into `derive_policy_evolution_proposals_from_report` and `report_contains_derivable_policy_record` so candidate-sensitive controls become required for applyable policy proposal derivation.

PRD promise: P1, P2, P3.

Public boundary for first RED test: `derive_policy_evolution_proposals_from_report`.

Acceptance criteria:

- [ ] No-op and harmful controls block proposals with derivation skipped events.
- [ ] Known-good control evidence plus positive empty-floor delta can derive a draft proposal.
- [ ] Candidate artifact requirements and changed-files checks remain enforced.
- [ ] Derived proposals still require human approval and record report-only authority fields.

## Slice 5: Ledger Events, Receipts, and Regression Coverage

Priority: P2
Estimate: medium

Scope: Emit evaluator-quality started and completed events, record supervisor-generated receipts, and add regression tests covering the observed saturated replay failure plus existing AutoResearch invariants.

PRD promise: P1, P2, P3, P4.

Public boundary for first RED test: AutoResearch orchestrator report generation with a fake event writer.

Acceptance criteria:

- [ ] `autoresearch_evaluator_quality_control_started` and completed events are emitted.
- [ ] Control receipts use supervisor provenance and runtime-native evidence grade when generated in-process.
- [ ] The observed saturated replay pattern remains blocked from policy proposal derivation.
- [ ] Existing AutoResearch, policy evolution, and workflow tests remain green.
