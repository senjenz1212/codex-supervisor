# TDD Plan: Terminal-Bench 2.1 Harness Benchmark Adapter

## test_terminal_bench_pilot_sample_loads_fixed_seed_manifest

Maps to: Slice 2; PRD P2

RED: Loading the Terminal-Bench pilot manifest fails because no loader or
manifest exists.

GREEN: `load_terminal_bench_pilot_sample` returns schema version, dataset id
`terminal-bench/terminal-bench-2-1`, seed `20260603`, model `gpt-5.5`, k=5, and
exactly thirty unique task ids.

## test_terminal_bench_report_computes_pass_metrics_and_noise_floor

Maps to: Slice 3; PRD P3

RED: No report builder can compute pass@1 confidence intervals, pass@5, pass^5,
delta, and the three-point noise-floor verdict from fixed trial outcomes.

GREEN: `build_terminal_bench_report` returns per-arm metrics, harness-minus-
baseline delta, confidence interval, `clears_noise_floor`, and a deterministic
report hash for a fixture with known outcomes.

## test_terminal_bench_report_is_report_only

Maps to: Slice 3 and Slice 5; PRD P5

RED: The report either lacks policy fields or can imply a default policy change.

GREEN: The report always includes `default_change_allowed=False`,
`config_mutated=False`, and `policy_mutated=False`; the test also verifies
`AgenticLeadCfg()` is not changed by report generation.

## test_terminal_bench_harbor_agent_dry_run_records_context

Maps to: Slice 1; PRD P1, P5

RED: Importing or running the Harbor agent requires Harbor or live workflow
execution.

GREEN: The agent imports without Harbor installed, runs against a fake
environment in dry-run mode, writes a transcript record, preserves model
`gpt-5.5`, and updates context metadata without mutating config.

## test_terminal_bench_pilot_script_refuses_live_without_budget

Maps to: Slice 4; PRD P4, P5

RED: The pilot script has no live cost guard and could start Harbor without an
explicit budget.

GREEN: `build_terminal_bench_pilot_plan` and the script's argument path refuse
live execution unless both `--allow-live` and `--max-budget-usd > 0` are
provided.

## test_terminal_bench_pilot_script_builds_harbor_commands

Maps to: Slice 4; PRD P4

RED: There is no deterministic command plan for baseline and harness arms.

GREEN: The plan contains stock Terminus-2 and harness `--agent-import-path`
commands, model `gpt-5.5`, k=5, the fixed task ids, jobs directories, and
report-only output paths.
