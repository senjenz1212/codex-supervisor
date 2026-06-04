# Implementation Plan: SWE-bench Pro Harness Benchmark Adapter

## Files / Modules To Touch

- `supervisor/swe_bench_eval.py`
- `supervisor/swe_bench_solver.py`
- `scripts/run_swe_bench_pro_pilot.py`
- `tests/test_swe_bench_pro_eval.py`
- `tests/fixtures/swe_bench_pro/pilot_sample.yaml`
- `tests/fixtures/swe_bench_pro/pilot_results.json`
- `docs/dual-agent/bench-swebench-pro-opus48-20260603/test-evidence.md`

## Steps

1. Add the fixed pilot sample loader and validation in `supervisor/swe_bench_eval.py`.
2. Add the SWE-bench Pro report builder with pass@1, pass@5, pass^5, confidence intervals, harness-minus-baseline delta, and noise-floor logic.
3. Add the solver adapter primitives in `supervisor/swe_bench_solver.py` for capturing a repo diff as `{instance_id, model_patch}` and converting it to the evaluator's `{instance_id, patch, prefix}` shape.
4. Add the dry-run/live pilot planning script with explicit live authorization and budget guards.
5. Add deterministic sample/results fixtures, focused tests, and generated replay report evidence.
6. Run focused pytest, py_compile, and git diff checks.

## Risks

- The live SWE-bench Pro runner is expensive and Docker-heavy. The pilot script must default to replay/report mode and require both `--allow-live` and a positive budget before executing commands.
- The external evaluator expects patch rows, while the solver contract emits `model_patch`. The adapter must preserve the solver row and provide an explicit evaluator conversion.
- A 30-task pilot can be noisy. The report must show the confidence interval and avoid recommending a full-set run unless the lower bound clears the three-point noise floor.
- The harness model route must stay fixed to Opus 4.8 for both arms; the plan asserts the current `opus` underlying model constant before building live commands.

## Traceability

- P1 -> `test_swe_bench_solver_captures_model_patch_jsonl`
- P1 -> `test_swe_bench_solver_rejects_missing_instance_id`
- P2 -> `test_swe_bench_pilot_plan_uses_same_model_budget_and_opus48_route`
- P3 -> `test_swe_bench_sample_loads_fixed_seed_manifest`
- P4 -> `test_swe_bench_report_computes_pass_metrics_and_noise_floor`
- P5 -> `test_swe_bench_report_is_report_only`
- P5 -> `test_swe_bench_pilot_script_refuses_live_without_budget`
- P5 -> `test_swe_bench_pilot_script_builds_replay_report`
