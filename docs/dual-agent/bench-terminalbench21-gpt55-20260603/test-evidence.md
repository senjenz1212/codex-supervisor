# Test Evidence: Terminal-Bench 2.1 GPT-5.5 Harness Benchmark Adapter

Task id: `bench-terminalbench21-gpt55-20260603`

## External/Local Probe Evidence

- Harbor CLI probe:
  - `uvx --from harbor harbor run --help`
  - Result: `--agent-import-path`, `--agent terminus-2`, `--model/-m`,
    `--n-attempts/-k`, `--dataset/-d`, and repeated
    `--include-task-name/-i` are supported by the installed Harbor CLI.
- Harbor agent API probe:
  - `uvx --from harbor python - <<'PY' ... from harbor.agents.base import BaseAgent ...`
  - Result: `BaseAgent` exposes `name`, `version`, async `setup`, async `run`,
    and `import_path`.
- Dataset probe:
  - `uvx --from harbor harbor download terminal-bench/terminal-bench-2-1 --output-dir .scratch/tb21-probe --export`
  - Result: 89 tasks downloaded. The committed sample is the seed `20260603`
    random sample of 30 task ids, sorted for stable display.

## Local Validation

- Planning validator:
  - `uv run python - <<'PY' ... validate_planning_artifacts(...) ... PY`
  - Result: `accepted`.
- Focused tests:
  - `uv run pytest tests/test_terminal_bench_eval.py -q`
  - Result: 6 passed in 0.26s.
- Related tests:
  - `uv run pytest tests/test_terminal_bench_eval.py tests/test_target_config_load.py -q`
  - Result: 12 passed in 0.21s.
- Compile:
  - `uv run python -m py_compile supervisor/terminal_bench_eval.py supervisor/terminal_bench_harbor_agent.py scripts/run_terminal_bench_pilot.py`
  - Result: passed.
- Whitespace:
  - `git diff --check`
  - Result: passed.

## Replay Pilot Report

This is a deterministic replay fixture report, not a paid live Harbor pilot. The
live command path is intentionally blocked unless an operator supplies
`--allow-live --max-budget-usd <positive>` and optionally `--run-live`.

- Command:
  - `uv run python scripts/run_terminal_bench_pilot.py --sample tests/fixtures/terminal_bench/pilot_sample.yaml --results tests/fixtures/terminal_bench/pilot_results.json --output-dir docs/dual-agent/bench-terminalbench21-gpt55-20260603/terminal-bench-pilot`
- Result:
  - dataset: `terminal-bench/terminal-bench-2-1`
  - model: `gpt-5.5`
  - task_count: 30
  - k: 5
  - planned_runs: 300
  - baseline pass@1 mean: 0.666667, 95% CI [0.587896, 0.737114]
  - harness pass@1 mean: 0.72, 95% CI [0.643342, 0.785671]
  - baseline pass@5/pass^5: 1.0 / 0.1
  - harness pass@5/pass^5: 1.0 / 0.1
  - delta pass@1: 0.053333, 95% CI [-0.050851, 0.157518]
  - noise-floor verdict: `inconclusive_or_null`
  - report_sha256:
    `bf96be86d4971979d41db1c9d3628dea3133265077b7ad2723b84565636fa6d7`

## Policy Invariant

- This slice adds a report-only adapter and deterministic replay report.
- `supervisor/config.py` is not modified by this slice.
- Report fields preserve:
  - `default_change_allowed=false`
  - `config_mutated=false`
  - `policy_mutated=false`
