# TDD Plan: AutoResearch Evaluator Quality Foundation

## RED / GREEN Strategy

Red: add public-boundary tests that reproduce the current failure mode: evaluator execution returns saturated all-pass trials, validation accepts report-only evidence, and policy derivation correctly produces no proposal. Then add failing tests for no-op, harmful, and known-good controls at validation and derivation boundaries.

Green: implement the smallest evaluator-quality schema, fixture controls, deterministic verification, and policy-derivation guard needed to pass those tests. Keep AutoResearch report-only, avoid live provider calls, and leave candidate-generation work out of this slice.

## test_autoresearch_noop_control_blocks_policy_proposal

Maps to: P1, P3, Slice 1, Slice 4.

- Boundary: `supervisor.autoresearch.policy_evolution.derive_policy_evolution_proposals_from_report`.
- Red: a report with a no-op candidate and positive-looking candidate metric can derive a proposal.
- Green: no-op control evidence with non-positive delta blocks proposal derivation and writes a derivation-skipped reason.

## test_autoresearch_harmful_control_blocks_policy_proposal

Maps to: P1, P3, Slice 2, Slice 4.

- Boundary: `derive_policy_evolution_proposals_from_report`.
- Red: a harmful or irrelevant candidate record can become applyable when the main metric appears positive.
- Green: harmful control regression blocks proposal derivation and preserves report-only authority fields.

## test_autoresearch_known_good_control_allows_candidate_sensitive_derivation

Maps to: P1, P3, Slice 2, Slice 4.

- Boundary: `derive_policy_evolution_proposals_from_report`.
- Red: there is no way to express a known-good seeded control that unlocks derivation after proving positive delta.
- Green: known-good control evidence, positive empty-floor delta, clean gaming flags, and a listed policy overlay candidate produce a draft proposal only.

## test_autoresearch_saturated_zero_variance_replay_stays_non_applyable

Maps to: P2, Slice 3, Slice 5.

- Boundary: `supervisor.autoresearch.validation.validate_attempt` and policy derivation.
- Red: `metric_trials=[1.0, 1.0, 1.0]` can clear proposal derivation because the evaluator executed.
- Green: validation records `zero_variance_trials`, policy derivation returns no proposals, and the skipped reason cites missing control-validated delta.

## test_autoresearch_determinism_requires_repeated_output_hash_match

Maps to: P4, Slice 3.

- Boundary: evaluator-quality control execution.
- Red: caller metadata claiming deterministic behavior is accepted without repeated execution evidence.
- Green: deterministic verification runs the same evaluator input twice, stores normalized output hashes, and accepts only matching hashes as determinism evidence.

## test_autoresearch_self_declared_deterministic_metadata_is_not_authoritative

Maps to: P2, P4, Slice 3.

- Boundary: report validation and policy derivation.
- Red: a report field such as `deterministic=true` suppresses `zero_variance_trials` or allows derivation.
- Green: unverified metadata is ignored or rejected, and proposal derivation still requires evaluator-quality controls.

## test_autoresearch_candidate_must_affect_evaluated_path

Maps to: P1, P2, Slice 1, Slice 2.

- Boundary: `validate_attempt` with held-out control metadata.
- Red: a candidate artifact listed in `changed_files` but irrelevant to the evaluated path can pass the quality floor.
- Green: candidate-path coverage is required, and irrelevant candidates fail or regress through the harmful control.

## test_autoresearch_evaluator_quality_events_and_receipts_are_emitted

Maps to: P1, P3, Slice 5.

- Boundary: AutoResearch orchestrator with a fake state event writer.
- Red: quality controls run silently or only write filesystem artifacts.
- Green: started and completed events plus supervisor provenance receipts are emitted with control refs, hashes, deltas, and verdicts.

## test_autoresearch_report_only_invariants_survive_quality_success

Maps to: P3, Slice 4, Slice 5.

- Boundary: report builder and derived proposal payload.
- Red: successful controls can set `default_change_allowed`, `policy_mutated`, or `gate_advanced` to true.
- Green: all outputs keep those fields false and require operator approval.

## Regression Commands

- `.venv/bin/pytest tests/test_autoresearch.py tests/test_autoresearch_policy_evolution.py -q`
- `.venv/bin/pytest tests/test_auto_evolution_loop.py -q`
- `.venv/bin/pytest tests/test_dual_agent_workflow_driver.py -k autoresearch -q`
- `.venv/bin/pytest tests/test_planning_validator.py -q`
- `git diff --check`
