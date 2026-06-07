# Test Evidence: Supervisor AutoResearch Foundation

## Focused AutoResearch Tests

- Command: `uv run pytest tests/test_autoresearch.py -q`
- Result: `16 passed in 0.55s`
- Covers: ledger event emission, immutable path rejection, mutable-only
  validation acceptance, missing evidence flags, median/IQR report metrics,
  report-only invariant, artifact hash reference validation, non-canonical path
  traversal rejection, absolute immutable path rejection, evaluator hash
  validation, production config mutation rejection, fixture runner live-call
  guard, report export, no gate advancement, and Cursor reviewer default
  compatibility.

## Compile Check

- Command: `uv run python -m py_compile supervisor/autoresearch/schema.py supervisor/autoresearch/orchestrator.py supervisor/autoresearch/validation.py supervisor/autoresearch/report.py scripts/run_supervisor_autoresearch.py tests/test_autoresearch.py`
- Result: passed with no output.

## Full Suite

- Command: `uv run --extra dev pytest -q`
- Result: `762 passed, 8 skipped in 122.19s`

## Fixture Runner Smoke

- Command: `uv run python scripts/run_supervisor_autoresearch.py --fixture tests/fixtures/autoresearch/fixture_experiment.json --output-dir <tmpdir> --run-id sample-autoresearch-run`
- Result: emitted `default_change_allowed=false`, six AutoResearch ledger event
  ids, `validation_status=accepted`, `metric_median=0.87`,
  `gaming_flags=[]`, and report sha256
  `878dbb8413f95a0030980c651aef1412731969d7f9faab509bef63087ae7c8c8`.
