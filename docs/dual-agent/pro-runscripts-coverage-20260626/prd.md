# PRD: Pro Run Scripts Coverage

Task id: `pro-runscripts-coverage-20260626`

## Problem Statement

The SWE-bench Pro Docker adapter requires bespoke per-instance `run_script.sh`
and `parser.py` files. Only three instance directories are vendored in this
repository, so any selected instance outside that set currently reaches the
oracle adapter and becomes `oracle_unavailable` with `pro_script_missing`.
That is honest, but too late for benchmark execution planning: a real Pro run
should know before oracle execution whether every selected instance has scripts.

## Solution

Expose a public preflight that checks a selected instance roster against the
resolved Pro `run_scripts/` root, and wire that preflight into official Pro
replay before prediction materialization, replay freeze, or oracle execution.
`SWEBENCH_PRO_ORACLE_SCRIPTS_DIR` or an explicit scripts-dir parameter can point
at a full clone of `scaleapi/SWE-bench_Pro-os/run_scripts`; otherwise the
adapter remains fail-closed against the partial vendored default.

## Promise Contracts

### P1. Selected Instances Resolve Their Scripts

Public boundary: `run_swe_bench_pro_oracle(...)` script resolution.

Chosen seam: `preflight_swe_bench_pro_run_scripts(...)` using the same scripts
root resolved by `swe_bench_pro_oracle_scripts_dir(...)`.

Allowed: each selected `instance_id` has both `run_script.sh` and `parser.py`;
the adapter copies those source files into the workspace and records source
hash evidence.

Forbidden: silently skipping a missing instance; deriving or fabricating a
generic script from a dataset row.

### P2. Fail-Fast Preflight

Public boundary: `swebench_mergeability_official_replay_runner(...)` after
`selected_instance_ids` is known and before replay/oracle work.

Chosen seam: a batch call to `preflight_swe_bench_pro_run_scripts(...)` over
the selected instance list.

Allowed: explicit `pro_script_missing` errors listing every missing selected
instance and missing file set.

Forbidden: discovering missing scripts one row at a time after frozen replay
work has started.

## User Stories

1. As a benchmark operator, I want missing Pro scripts reported before a run so
   that I do not spend time materializing repos or running Docker for an
   impossible selection.
2. As an evaluator maintainer, I want the adapter receipt to identify the exact
   source scripts used so that script provenance is auditable.
3. As a future corpus builder, I want selected-instance coverage to fail closed
   so that a headline benchmark cannot quietly exclude unprepared rows.

## Out Of Scope

- Building or modifying the SWE-bench Pro oracle logic.
- Building the solver, reviewer panel, powered stats, or benchmark-to-policy
  bridge.
- Committing the full upstream `run_scripts/` corpus into this repository.

