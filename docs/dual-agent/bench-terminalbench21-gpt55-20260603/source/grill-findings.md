# PRD Grill Findings: Terminal-Bench 2.1 Harness Benchmark Adapter

### Finding 1 - Canonical Harbor dataset name differs from prompt shorthand

Status: resolved

The prompt uses `terminal-bench@2.1`, but a local Harbor registry probe found the
current downloadable dataset as `terminal-bench/terminal-bench-2-1@latest`.
The PRD now treats the former as operator shorthand and records the latter as the
canonical command target, so the adapter will not ship an un-runnable command.

### Finding 2 - Live pilot cost must not be hidden inside tests

Status: resolved

The requested pilot is roughly 30 tasks times five trials times two arms. That is
large enough that tests must never launch it implicitly. The PRD now requires
`--allow-live` plus a positive `--max-budget-usd`, and the default path is a
dry-run/replay report with explicit Harbor commands.

### Finding 3 - Harness terminal control is the real integration risk

Status: resolved

Harbor agents execute against an environment object, while codex-supervisor's
workflow normally runs in the repository worktree. The implementation plan must
keep the live bridge narrow: produce or replay commands through the Harbor agent
environment and measure only Harbor verifier results. This is recorded as an
adapter boundary risk rather than a reason to alter supervisor core policy.

### Finding 4 - Published leaderboard numbers are not pilot baselines

Status: resolved

The 83.4 percent and 78.9 percent figures are external reference context. The
pilot baseline is the local Terminus-2 arm on the same fixed 30-task sample,
same GPT-5.5 model, and same budget. The PRD forbids using the published number
as the measured pilot baseline.
