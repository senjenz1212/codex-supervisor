# TDD Grill Findings: Agentic Eval Bridge

### Finding 1: The First Test Must Hit The Bridge Public Boundary

status: resolved

The TDD plan starts at `assemble_agentic_eval_task`, not at helper-only budget
math. Budget tests follow, but the first proof exercises the operator-facing
conversion from labeled case to runner-loadable dataset.

### Finding 2: Replay Determinism Needs The Existing Runner

status: resolved

The TDD plan now requires replay through `agentic_eval_runner` and compares
`report_sha256`, so the bridge is tested against the same consumer that will
drive fan-out decisions.

### Finding 3: Live Calls Need A Negative CLI Test

status: resolved

The plan includes an explicit CLI refusal test for missing `--allow-live-calls`,
which protects the default test and replay paths from provider execution.
