# Implementation Plan: AutoResearch Evaluator Quality Foundation

## Files / Modules To Touch

- `supervisor/autoresearch/schema.py`
- `supervisor/autoresearch/validation.py`
- `supervisor/autoresearch/report.py`
- `supervisor/autoresearch/orchestrator.py`
- `supervisor/autoresearch/policy_evolution.py`
- `supervisor/autoresearch/evaluator.py`
- `supervisor/autoresearch/evaluators/replay_corpus.py`
- `tests/test_autoresearch.py`
- `tests/test_autoresearch_policy_evolution.py`
- `tests/test_auto_evolution_loop.py`
- `tests/fixtures/autoresearch/`

## Approach

1. Add evaluator-quality control payloads to AutoResearch records with stable JSON serialization and conservative defaults.
2. Add fixture controls for no-op, harmful, and known-good candidates. Keep them under test fixtures or immutable evaluator input paths rather than candidate mutable paths.
3. Add deterministic verification by repeated same-input evaluator execution and normalized output hash comparison.
4. Integrate quality checks into validation output and policy derivation so saturated or unproven records remain non-applyable.
5. Emit evaluator-quality started and completed events plus supervisor-originated receipts from the orchestrator path.
6. Preserve existing report-only invariants and human approval requirements.

## Risks

- The held-out corpus can become too easy and reproduce the saturated replay problem. The control tests must prove no-op and harmful candidates do not improve.
- The derivation gate can become too strict and block every candidate. The known-good seeded fixture must prove the success path stays reachable.
- Determinism metadata can become a trust stamp. The implementation must verify output hashes by execution.
- Adding fields to report records can break existing report readers. New fields should be optional for historical reports and required only for applyable proposal derivation.

## Traceability

- P1 -> test_autoresearch_noop_control_blocks_policy_proposal
- P1 -> test_autoresearch_harmful_control_blocks_policy_proposal
- P1 -> test_autoresearch_known_good_control_allows_candidate_sensitive_derivation
- P2 -> test_autoresearch_saturated_zero_variance_replay_stays_non_applyable
- P2 -> test_autoresearch_candidate_must_affect_evaluated_path
- P3 -> test_autoresearch_report_only_invariants_survive_quality_success
- P4 -> test_autoresearch_determinism_requires_repeated_output_hash_match
- P4 -> test_autoresearch_self_declared_deterministic_metadata_is_not_authoritative
- P1 -> test_autoresearch_evaluator_quality_events_and_receipts_are_emitted

## Validation

- Run the named AutoResearch and policy evolution tests through `.venv/bin/pytest`.
- Run `tests/test_planning_validator.py` to ensure the PRD-to-TDD contract remains valid.
- Run `git diff --check` before outcome review.
- Confirm Cursor SDK rigorous review is requested for `tdd_review`, `implementation_plan`, and `outcome_review`.
