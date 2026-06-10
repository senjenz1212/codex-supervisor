# Implementation Plan

## Files / Modules To Touch

- `supervisor/autoresearch/schema.py`
- `supervisor/autoresearch/evaluator.py`
- `supervisor/autoresearch/orchestrator.py`
- `supervisor/autoresearch/validation.py`
- `scripts/run_supervisor_autoresearch.py`
- `tests/test_autoresearch.py`
- `tests/fixtures/autoresearch/fixture_experiment.json`
- `tests/fixtures/autoresearch/locked/evaluator.py`

## Steps

1. Extend schema payloads with timeout and evaluator execution provenance fields.
2. Add a live evaluator runner that verifies script hash, creates an isolated attempt worktree, runs `k_trials`, writes an evaluator-run artifact, and reports mutable-scope violations.
3. Update orchestration to emit attempt execution job events and replace fixture metrics with evaluator-computed metrics in live mode.
4. Update validation to reject non-executed metrics, dangling evidence refs, hash mismatches, and mutable escape errors while preserving report-only invariants.
5. Update the CLI to pass `execution_mode` through to the orchestrator.

## Risks

- A hash check after subprocess launch would be too late, so the runner must verify before command construction.
- Worktree isolation is not a full OS sandbox; this slice detects and rejects side effects in the isolated tree, but it does not claim hostile-code containment.
- Absolute evaluator artifact paths are useful locally but should be made portable in a later replay-manifest slice.
- Live execution must remain opt-in because evaluators are executable local code.

## Traceability

- P1 maps to `test_autoresearch_live_evaluator_hash_mismatch_blocks_execution` and `test_autoresearch_cli_allow_live_executes_evaluator`.
- P2 maps to `test_autoresearch_live_evaluator_runs_k_trials_and_records_iqr` and `test_autoresearch_validation_rejects_fixture_metrics_without_evaluator_execution`.
- P3 maps to `test_autoresearch_live_evaluator_blocks_mutable_path_escape`.
- P4 maps to `test_autoresearch_validation_flags_dangling_evidence_ref` and `test_autoresearch_validation_flags_zero_variance_trials`.
- P5 maps to `test_autoresearch_report_only_invariants_remain_false_for_live_run` and the existing report-only validation tests.
