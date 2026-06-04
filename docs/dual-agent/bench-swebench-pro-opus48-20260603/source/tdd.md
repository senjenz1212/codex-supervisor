# TDD Plan

## test_swe_bench_sample_loads_fixed_seed_manifest

Boundary: `swe_bench_pilot_sample_loader`.

Maps to: P3, Slice 1.

RED: With no loader, the fixed sample fixture cannot be validated.

GREEN: `load_swe_bench_pilot_sample` returns schema version, dataset id, source
count `731`, seed `20260603`, model `claude-opus-4-8`, `k=5`, and exactly 30
unique instance ids.

## test_swe_bench_report_computes_pass_metrics_and_noise_floor

Boundary: `swe_bench_report_builder`.

Maps to: P4, P5, Slice 1.

RED: No report builder computes pass@1/pass@5/pass^5 or delta CI.

GREEN: Fixture results produce deterministic baseline/harness pass metrics,
harness-minus-baseline delta, and `inconclusive_or_null` when the CI lower
bound does not clear the 3pt noise floor.

## test_swe_bench_report_is_report_only

Boundary: `swe_bench_report_builder`.

Maps to: P5, Slice 1.

RED: No invariant proves the report builder leaves `AgenticLeadCfg` and
production defaults untouched.

GREEN: Building a report leaves `AgenticLeadCfg().model_dump()` unchanged and
sets `default_change_allowed=false`, `config_mutated=false`, and
`policy_mutated=false`.

## test_swe_bench_solver_captures_model_patch_jsonl

Boundary: `swe_bench_solver_adapter`.

Maps to: P1, Slice 2.

RED: No solver adapter can emit `{instance_id, model_patch}` from a checked-out
repo diff.

GREEN: A temp git repo with a one-line edit yields a JSONL row containing the
instance id and a patch with the expected diff hunk.

## test_swe_bench_solver_rejects_missing_instance_id

Boundary: `swe_bench_solver_adapter`.

Maps to: P1, Slice 2.

RED: Missing ids can produce evaluator rows that cannot be graded.

GREEN: The solver adapter rejects blank `instance_id` before writing patch rows.

## test_swe_bench_pilot_plan_uses_same_model_budget_and_opus48_route

Boundary: `swe_bench_pilot_plan`.

Maps to: P1, P2, P5, Slice 2.

RED: The baseline and harness command plans might diverge on model or budget.

GREEN: `build_swe_bench_pilot_plan` returns 300 planned runs, same model,
same per-run budget, mini-swe-agent baseline command, codex-supervisor harness
command, and records `claude-opus-4-8` as the Opus route.

## test_swe_bench_pilot_script_refuses_live_without_budget

Boundary: `swe_bench_pilot_cli`.

Maps to: P5, Slice 3.

RED: CLI could trigger a paid live run accidentally.

GREEN: `--run-live` without `--allow-live` exits rc 2; `allow_live=True` with
zero budget raises `ValueError`.

## test_swe_bench_pilot_script_builds_replay_report

Boundary: `swe_bench_pilot_cli`.

Maps to: P4, P5, Slice 3.

RED: No CLI path produces deterministic replay artifacts.

GREEN: Running the CLI with sample + fixture results writes `pilot-plan.json`,
`report.json`, and `rows.jsonl` with a stable `report_sha256`.
