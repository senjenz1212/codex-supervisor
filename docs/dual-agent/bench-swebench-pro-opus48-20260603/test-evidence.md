# Test Evidence

Task: `bench-swebench-pro-opus48-20260603`

## Commands

- `uv run pytest tests/test_swe_bench_pro_eval.py -q`
  - Result: `8 passed in 1.14s`
- `uv run python scripts/run_swe_bench_pro_pilot.py --sample tests/fixtures/swe_bench_pro/pilot_sample.yaml --results tests/fixtures/swe_bench_pro/pilot_results.json --output-dir docs/dual-agent/bench-swebench-pro-opus48-20260603/swe-bench-pro-pilot`
  - Result: report exported with `report_sha256=37eec934521ab760d139d986d7faa659e60d1a3a980e4f5086d85ba4dc75af63`
- `uv run pytest tests/test_swe_bench_pro_eval.py tests/test_target_config_load.py -q`
  - Result: `14 passed in 1.20s`
- `uv run python -m py_compile supervisor/swe_bench_eval.py supervisor/swe_bench_solver.py scripts/run_swe_bench_pro_pilot.py`
  - Result: passed
- `git diff --check`
  - Result: passed
- `uv run pytest -q`
  - Result: `698 passed in 281.96s`

## Replay Summary

- Dataset: `ScaleAI/SWE-bench_Pro`, split `test`
- Seed: `20260603`
- Instance count: `30`
- Trials per arm: `5`
- Planned runs: `300`
- Model: `claude-opus-4-8`
- Baseline pass@1: `0.64`
- Harness pass@1: `0.70`
- Delta: `0.06`, CI95 `[-0.046202, 0.166202]`
- Noise-floor verdict: `inconclusive_or_null`
- Report-only: `default_change_allowed=false`, `policy_mutated=false`
