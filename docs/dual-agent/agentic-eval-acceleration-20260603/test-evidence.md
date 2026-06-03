# Test Evidence: Agentic Eval Acceleration

Task id: `agentic-eval-acceleration-20260603`

## Commands

1. `uv run pytest tests/test_agentic_eval.py -q`
   - Result: `17 passed in 0.59s`

2. `uv run pytest tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py tests/test_agentic_eval_corpus.py -q`
   - Result: `33 passed in 1.30s`

3. `uv run python -m py_compile supervisor/agentic_eval.py supervisor/agentic_eval_assembler.py && git diff --check`
   - Result: passed

4. `uv run --extra dev pytest -q`
   - Result: `678 passed in 108.24s (0:01:48)`

## Parallelism Corpus Replay

- Dataset:
  `docs/dual-agent/agentic-eval-acceleration-20260603/agentic-eval-parallelism/agentic_eval_three_arm_dataset.yaml`
- Report:
  `docs/dual-agent/agentic-eval-acceleration-20260603/agentic-eval-parallelism/report/report.json`
- Report SHA256:
  `bb6c7ba4d09556af61b0adabfaad07e772204d1a49484536c6eb8204d72e8955`

## Acceleration Summary

| Mode | p50 | p95 | Qualifies | Failing Predicates |
| --- | ---: | ---: | --- | --- |
| `lead_direct` | `1.0` | `1.0` | `false` | `[]` |
| `agentic_allowed` | `1.349` | `1.421` | `true` | `[]` |
| `agentic_required` | `1.453` | `1.53` | `true` | `[]` |

## Report-Only Checks

- `default_change_allowed`: `false`
- `agentic_lead_policy_snapshot.policy`: `off`
- `recommendation.report_only`: `true`
- `recommendation.policy_mutated`: `false`
