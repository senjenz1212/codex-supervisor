# Test Evidence: Agentic Eval Trials And Strata

Task id: `agentic-eval-trials-strata-20260603`

## Planning Validation

- Command: `uv run python - <<'PY' ... validate_planning_artifacts(...) ... PY`
- Result: passed
- Summary: required planning artifact kinds present; PRD, issues, TDD,
  grill-findings, and implementation-plan checks passed.

## Focused Tests

- Command: `uv run pytest tests/test_agentic_eval.py -q`
- Result: passed
- Summary: `22 passed in 0.92s`

## Neighboring Eval Tests

- Command: `uv run pytest tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py tests/test_agentic_eval_corpus.py -q`
- Result: passed
- Summary: `38 passed in 1.58s`

## Hygiene

- Command: `uv run python -m py_compile supervisor/agentic_eval.py && git diff --check`
- Result: passed

## Full Suite

- Command: `uv run --extra dev pytest -q`
- Result: passed
- Summary: `683 passed in 152.97s (0:02:32)`
