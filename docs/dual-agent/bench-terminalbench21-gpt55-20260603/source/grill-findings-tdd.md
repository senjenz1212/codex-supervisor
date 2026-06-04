# TDD Grill Findings: Terminal-Bench 2.1 Harness Benchmark Adapter

### Finding 1 - Boundary tests must not mock away the Harbor adapter

Status: resolved

The TDD plan originally risked testing only helper functions. The plan now names
`test_terminal_bench_harbor_agent_dry_run_records_context`, which imports the
public Harbor-agent class and calls `run` against a fake environment so the
adapter boundary is exercised directly.

### Finding 2 - Report metrics need repeated-trial semantics

Status: resolved

Terminal-Bench uses k=5 attempts. The report test now requires pass@1 mean from
all trials and per-task pass@5/pass^5 rollups so the repeated-trial semantics are
explicit rather than a single binary row per task.

### Finding 3 - Cost guard must be a failing test, not prose

Status: resolved

The TDD plan now includes
`test_terminal_bench_pilot_script_refuses_live_without_budget`, proving live mode
is rejected without both `--allow-live` and a positive budget cap.

### Finding 4 - Policy invariant must account for the repo's current defaults

Status: resolved

The benchmark must not change whatever `AgenticLeadCfg` currently declares. The
test checks the report-only flags and that report generation itself leaves
`AgenticLeadCfg()` unchanged instead of asserting a hard-coded old default.
