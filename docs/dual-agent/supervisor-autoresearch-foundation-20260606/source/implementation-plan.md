# Implementation Plan: Supervisor AutoResearch Foundation

## Files / Modules To Touch

- `supervisor/autoresearch/__init__.py`
- `supervisor/autoresearch/schema.py`
- `supervisor/autoresearch/validation.py`
- `supervisor/autoresearch/report.py`
- `supervisor/autoresearch/orchestrator.py`
- `scripts/run_supervisor_autoresearch.py`
- `tests/test_autoresearch.py`
- `tests/fixtures/autoresearch/fixture_experiment.json`
- `docs/dual-agent/supervisor-autoresearch-foundation-20260606/source/prd.md`
- `docs/dual-agent/supervisor-autoresearch-foundation-20260606/source/tdd.md`

## Steps

1. Add dataclass schemas with `to_payload` helpers and stable JSON hashing.
2. Implement validation for mutable and immutable changed-file surfaces,
   artifact hash checks, missing evidence references, and report-only status.
3. Implement report aggregation for repeated trial median, IQR, unstable metric
   flags, recommendation text, and `default_change_allowed=false`.
4. Implement the fixture orchestrator that writes additive ledger events through
   a supplied state-like object.
5. Add a fixture runner script that defaults to fixture replay and blocks live
   modes unless `--allow-live` is set.
6. Add tests covering the PRD promises and run focused plus full regression
   commands.

## Risks

- Path validation could be too permissive if it treats string prefixes
  imprecisely; normalize paths and compare exact path or directory containment.
- Report-only invariants could drift if future callers pass custom flags; hard
  code the first-slice reports to `default_change_allowed=false`.
- Ledger payloads could become non-deterministic if timestamps or unordered
  dictionaries enter hashes; sort JSON and let tests assert stable fields.
- Fixture runner could accidentally become a live workflow wrapper; keep live
  mode unimplemented except for the explicit guard.

## Traceability

- P1 is covered by `test_autoresearch_orchestrator_emits_experiment_and_attempt_events`.
- P2 is covered by `test_autoresearch_validation_rejects_immutable_path_mutation`
  and `test_autoresearch_validation_accepts_mutable_only_attempt`.
- P3 is covered by
  `test_autoresearch_report_reduces_trials_to_median_iqr_and_flags_unstable`
  and `test_autoresearch_report_is_report_only`.
- P4 is covered by
  `test_autoresearch_fixture_runner_blocks_live_calls_by_default`.
- P5 is covered by `test_autoresearch_validator_cannot_advance_gates` and
  `test_autoresearch_cursor_reviewer_defaults_remain_compatible`.
