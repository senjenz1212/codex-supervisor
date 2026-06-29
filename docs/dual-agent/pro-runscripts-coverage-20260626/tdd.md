# TDD Plan

Task id: `pro-runscripts-coverage-20260626`

## RED 1: Missing Scripts Reported

Test: `tests/test_pro_runscripts_coverage.py::test_missing_instance_scripts_reported`

Public boundary: `preflight_swe_bench_pro_run_scripts(...)`.

Expected RED: import fails because the preflight does not exist.

Minimal GREEN: add the preflight and resolved scripts-dir helper.

## RED 2: Present Scripts Resolve

Test: `tests/test_pro_runscripts_coverage.py::test_present_instance_scripts_resolve`

Public boundary: `preflight_swe_bench_pro_run_scripts(...)`.

Minimal GREEN: return resolved `run_script.sh` and `parser.py` paths with
script hash evidence.

## RED 3: Batch Replay Fails Before Oracle

Test:
`tests/test_swe_bench_pro_mergeability_bridge.py::test_official_replay_preflights_selected_pro_run_scripts_before_oracle`

Public boundary: `swebench_mergeability_official_replay_runner(...)`.

Minimal GREEN: call the preflight after `selected_instance_ids` is computed and
raise an explicit `pro_script_missing` error before materializer/oracle work.

## RED 4: Adapter Missing Scripts Never Run Docker

Test:
`tests/test_swe_bench_pro_oracle.py::test_pro_runner_missing_scripts_fails_before_docker`

Public boundary: `run_swe_bench_pro_oracle(...)`.

Minimal GREEN: reuse the preflight in the adapter and return unavailable with
`command=[]`.

## RED 5: Adapter Copies Bespoke Scripts Verbatim

Test:
`tests/test_swe_bench_pro_oracle.py::test_pro_runner_copies_source_scripts_verbatim`

Public boundary: `run_swe_bench_pro_oracle(...)`.

Minimal GREEN: copy `run_script.sh` and `parser.py` from the selected scripts
dir and record source hash evidence.

