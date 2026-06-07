# TDD Plan: Supervisor AutoResearch Foundation

## RED / GREEN Strategy

Red: add public-boundary tests for the orchestrator, validator, report builder,
and fixture runner before implementation. The first failures should prove there
is no AutoResearch domain package, no ledger-backed attempt event, no immutable
surface rejection, and no fixture-only runner.

Green: implement only the new `supervisor/autoresearch/` package, one script,
fixtures, and focused tests. Keep production workflow, config defaults, reviewer
aggregation, and gate code unchanged.

## test_autoresearch_orchestrator_emits_experiment_and_attempt_events

Maps to: P1, Slice 1.

- Boundary: `supervisor.autoresearch.orchestrator.run_autoresearch_fixture`.
- Red: package or function does not exist and no ledger events can be captured.
- Green: a fixture experiment emits experiment-started, attempt-started,
  attempt-completed, validation-started, validation-completed, and
  report-emitted events with stable IDs and statuses.

## test_autoresearch_validation_rejects_immutable_path_mutation

Maps to: P2, Slice 2.

- Boundary: `supervisor.autoresearch.validation.validate_attempt`.
- Red: an attempt changing `supervisor/state.py` can pass validation.
- Green: immutable authority changes produce `validation_status=rejected` and a
  gaming flag naming immutable path mutation.

## test_autoresearch_validation_accepts_mutable_only_attempt

Maps to: P2, Slice 2.

- Boundary: `validate_attempt`.
- Red: validator cannot distinguish mutable allowlisted changes from forbidden
  authority changes.
- Green: an attempt limited to allowed skill or fixture overlay paths validates
  without immutable-surface gaming flags.

## test_autoresearch_validation_flags_missing_evidence_refs

Maps to: P2, Slice 2.

- Boundary: `validate_attempt`.
- Red: an attempt with no replayable evidence references can validate.
- Green: missing evidence references reject the attempt and emit a
  `missing_evidence_refs` gaming flag.

## test_autoresearch_validation_rejects_missing_artifact_hash_ref

Maps to: P2, Slice 2.

- Boundary: `validate_attempt`.
- Red: a referenced artifact hash can point at a missing file without failing
  validation.
- Green: missing artifact hash references reject the attempt and emit an
  `artifact_hash_mismatch` gaming flag.

## test_autoresearch_validation_rejects_artifact_hash_mismatch

Maps to: P2, Slice 2.

- Boundary: `validate_attempt`.
- Red: a present artifact can diverge from its claimed hash without failing
  validation.
- Green: present artifact hash mismatches reject the attempt and emit an
  `artifact_hash_mismatch` gaming flag.

## test_autoresearch_validation_rejects_path_traversal_to_immutable_surface

Maps to: P2, Slice 2.

- Boundary: `validate_attempt`.
- Red: a changed file such as
  `skills/reviewer-rubrics/../../supervisor/state.py` can masquerade as a
  mutable-path change.
- Green: changed files are canonicalized/rejected before matching, the resolved
  immutable authority path is detected, and the attempt is rejected.

## test_autoresearch_validation_rejects_evaluator_hash_mismatch

Maps to: P2, P5, Slice 2.

- Boundary: `validate_attempt`.
- Red: `evaluator_ref` / `evaluator_hash` are passive metadata and a mutated
  evaluator fixture can still validate.
- Green: evaluator fixture hash mismatches reject the attempt and emit an
  `evaluator_hash_mismatch` gaming flag.

## test_autoresearch_report_reduces_trials_to_median_iqr_and_flags_unstable

Maps to: P3, Slice 3.

- Boundary: `supervisor.autoresearch.report.build_autoresearch_report`.
- Red: repeated metric trials are absent, averaged only, or instability is
  hidden.
- Green: trial metrics reduce to median and IQR, and differing trial values set
  `quality_unstable_across_trials=true`.

## test_autoresearch_report_is_report_only

Maps to: P3, P5, Slice 3.

- Boundary: `build_autoresearch_report`.
- Red: report lacks no-default-change fields or can mark default changes
  allowed.
- Green: report always includes `default_change_allowed=false`,
  `policy_mutated=false`, and a recommendation that requires operator review.

## test_autoresearch_fixture_runner_blocks_live_calls_by_default

Maps to: P4, Slice 4.

- Boundary: `scripts/run_supervisor_autoresearch.py`.
- Red: non-fixture execution can proceed without explicit live authorization.
- Green: default fixture mode runs from a JSON fixture, while non-fixture mode
  exits with a clear live-call guard unless `--allow-live` is supplied.

## test_autoresearch_fixture_runner_writes_report

Maps to: P4, Slice 4.

- Boundary: `scripts/run_supervisor_autoresearch.py`.
- Red: fixture replay does not export a deterministic validation report.
- Green: fixture replay writes `report.json` with accepted validation evidence
  and `default_change_allowed=false`.

## test_autoresearch_validator_cannot_advance_gates

Maps to: P5, Slice 5.

- Boundary: `validate_attempt` and report output.
- Red: validator exposes or emits accepted dual-agent gate decisions.
- Green: validator emits validation evidence only; no payload contains gate
  advancement authority.

## test_autoresearch_cursor_reviewer_defaults_remain_compatible

Maps to: P5, Slice 5.

- Boundary: `supervisor.config.Config`.
- Red: AutoResearch changes Cursor reviewer defaults or disables rigorous Cursor
  compatibility.
- Green: config still reports `reviewer_output_mode="cursor_sdk"` and
  AutoResearch reports no config mutation.

## Regression Commands

- `uv run pytest tests/test_autoresearch.py -q`
- `uv run python -m py_compile supervisor/autoresearch/schema.py supervisor/autoresearch/orchestrator.py supervisor/autoresearch/validation.py supervisor/autoresearch/report.py scripts/run_supervisor_autoresearch.py tests/test_autoresearch.py`
- `git diff --check`
- `uv run --extra dev pytest -q`
