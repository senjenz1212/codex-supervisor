# Implementation Plan: Terminal-Bench 2.1 Harness Benchmark Adapter

## Files / Modules To Touch

- `supervisor/terminal_bench_eval.py`
- `supervisor/terminal_bench_harbor_agent.py`
- `scripts/run_terminal_bench_pilot.py`
- `tests/test_terminal_bench_eval.py`
- `tests/fixtures/terminal_bench/pilot_sample.yaml`
- `tests/fixtures/terminal_bench/pilot_results.json`
- `docs/dual-agent/bench-terminalbench21-gpt55-20260603/test-evidence.md`

## Steps

1. Add the pilot sample loader and validation in `supervisor/terminal_bench_eval.py`.
2. Add the Terminal-Bench report builder with pass@ metrics, Wilson-style
   confidence intervals, per-task repeated-trial rollups, delta, and noise-floor
   logic.
3. Add the Harbor agent adapter with optional Harbor imports, dry-run mode,
   GPT-5.5 model metadata, and environment command transcript recording.
4. Add the dry-run/live pilot planning script with explicit live budget guard and
   Harbor commands for Terminus-2 and the harness adapter.
5. Add deterministic sample and trial fixtures, focused tests, and generated
   report evidence.
6. Run focused pytest, py_compile, planning validation, and git diff checks.

## Risks

- The true live harness bridge is heavier than a normal Harbor agent because
  codex-supervisor's gated workflow was designed around host worktrees, while
  Harbor tasks are solved by commands executed in a sandbox. The adapter must
  keep this as an explicit boundary instead of pretending a replay fixture is a
  live pilot.
- Harbor CLI and package APIs can drift. The code should use optional imports,
  command manifests, and result-file normalization rather than importing Harbor
  models in core report code.
- Running 300 live trials can be costly. The script must default to dry-run and
  require both live authorization and a budget cap.
- A pilot subset can be noisy. The report must identify whether the delta clears
  the three-point noise floor and avoid recommending policy changes from a null.

## Traceability

- P1 -> test_terminal_bench_harbor_agent_dry_run_records_context
- P2 -> test_terminal_bench_pilot_sample_loads_fixed_seed_manifest
- P3 -> test_terminal_bench_report_computes_pass_metrics_and_noise_floor
- P4 -> test_terminal_bench_pilot_script_refuses_live_without_budget
- P4 -> test_terminal_bench_pilot_script_builds_harbor_commands
- P5 -> test_terminal_bench_report_is_report_only
