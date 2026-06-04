# PRD: Terminal-Bench 2.1 Harness Benchmark Adapter

## Problem Statement

Operators need an honest, report-only way to compare the codex-supervisor
dual-agent harness against the stock Terminus-2 scaffold on Terminal-Bench 2.1
while holding the model fixed to GPT-5.5. The benchmark must not conflate model
strength, extra budget, or policy-default mutation with harness value. It also
must not accidentally launch a 300-run paid pilot from unit tests or from a
normal import path.

The current repo has agentic evaluation reporting patterns for replayable
datasets and report-only recommendations, but it does not expose a Harbor custom
agent class, a Terminal-Bench pilot sample manifest, or a Terminal-Bench
specific pass@ report for comparing harness-vs-baseline outcomes.

## Solution

Add a Terminal-Bench evaluation adapter layer with three public surfaces:

- A Harbor custom agent import path for the harness arm:
  `supervisor.terminal_bench_harbor_agent:CodexSupervisorTerminalBenchAgent`.
- A report-only Terminal-Bench pilot reporter that consumes recorded Harbor trial
  outcomes and emits pass@1, pass@5, pass^5, confidence intervals, delta versus
  Terminus-2, and a noise-floor verdict.
- A script that records the fixed seed, fixed task ids, budget cap, Harbor
  commands for the stock Terminus-2 baseline and harness arms, and can run live
  only with explicit `--allow-live` and a positive `--max-budget-usd`.

The default test path uses deterministic replay fixtures. The live Harbor path
is cost-gated and checkpoint/resume friendly by writing commands, manifests, and
partial job result paths before launching. The adapter must preserve the current
supervisor defaults and report-only invariant; it measures, never flips policy.

## User Stories

1. As an operator, I can import a Harbor agent class by path and have it route a
   Terminal-Bench task through codex-supervisor with GPT-5.5 selected for the
   harness arm.
2. As an evaluator, I can run a deterministic replay report over recorded
   Terminal-Bench trial results and see whether the harness clears a three-point
   noise floor versus stock Terminus-2.
3. As a cost-conscious operator, I can inspect a fixed 30-task, k=5 pilot plan
   before any paid or long-running Harbor job starts, with the exact task ids,
   model, arm commands, and budget cap recorded.
4. As a maintainer, I can verify in tests that the benchmark is report-only and
   that importing the adapter or running replay does not mutate supervisor
   policy defaults.

## PRD Promise Contracts

P1. Harbor Agent Adapter.
- User-visible promise: Harbor can load a codex-supervisor harness agent via an
  `--agent-import-path` compatible class and pass GPT-5.5 as the model.
- Representative action: instantiate
  `CodexSupervisorTerminalBenchAgent(logs_dir=..., model_name="gpt-5.5")` and run
  it against a fake Harbor environment.
- Public boundary: `supervisor.terminal_bench_harbor_agent.CodexSupervisorTerminalBenchAgent`.
- Allowed outcomes: writes a command transcript and updates the Harbor agent
  context with model, workflow, cost, and status metadata; live execution is
  skipped only when configured as dry-run.
- Forbidden outcomes: imports require Harbor in normal unit tests; model silently
  changes away from GPT-5.5; fan-out workers get write access; config defaults
  are mutated.

P2. Fixed Pilot Sample.
- User-visible promise: the pilot sample is fixed, seed-pinned, and derived from
  the current 89-task Terminal-Bench 2.1 dataset.
- Representative action: load the committed sample manifest.
- Public boundary: `supervisor.terminal_bench_eval.load_terminal_bench_pilot_sample`.
- Allowed outcomes: returns 30 ordered task ids, seed `20260603`, dataset
  `terminal-bench/terminal-bench-2-1`, and `k=5`.
- Forbidden outcomes: random sampling at report time; fewer than 30 task ids;
  hidden full-89 expansion; changing the sample without changing the manifest.

P3. Report-Only Metrics.
- User-visible promise: recorded baseline and harness trial results produce
  pass@1 mean, 95 percent confidence intervals, pass@5, pass^5, harness-minus-
  baseline delta, and a noise-floor verdict.
- Representative action: run the reporter on deterministic fixture results.
- Public boundary: `supervisor.terminal_bench_eval.build_terminal_bench_report`.
- Allowed outcomes: report includes per-arm metrics, per-task rows, confidence
  intervals, `default_change_allowed=False`, `policy_mutated=False`, and
  external-reference fields.
- Forbidden outcomes: live Harbor calls during report replay; hand-assigned
  scores; policy changes; declaring a win below the three-point noise floor.

P4. Cost Guard And Commands.
- User-visible promise: the live pilot command path requires explicit
  `--allow-live` and a positive per-run budget cap before launching Harbor.
- Representative action: run the script without `--allow-live`.
- Public boundary: `scripts/run_terminal_bench_pilot.py`.
- Allowed outcomes: writes or prints a dry-run plan with baseline and harness
  Harbor commands and exits without launching the jobs.
- Forbidden outcomes: accidental 300-run launch; missing budget cap; running the
  full 89-task set before pilot signal; non-detached job plan without output
  directories for checkpoint/resume.

P5. Supervisor Defaults Unchanged.
- User-visible promise: building and reporting this benchmark does not change
  codex-supervisor config or policy defaults.
- Representative action: inspect `AgenticLeadCfg()` before and after report
  generation.
- Public boundary: config object plus report fields.
- Allowed outcomes: defaults remain whatever the repo config declares at run
  time; report says policy/config were not mutated.
- Forbidden outcomes: modifying `supervisor/config.py` for the benchmark; writing
  global policy files; setting `default_change_allowed=True`.

## Implementation Decisions

- Use Harbor's current import contract (`harbor.agents.base.BaseAgent`) when the
  package is installed, and provide a local fallback base class so unit tests do
  not add a mandatory Harbor dependency.
- Treat `terminal-bench/terminal-bench-2-1` as the canonical dataset id observed
  by a local Harbor download; keep the user's `terminal-bench@2.1` phrasing as a
  documented alias only.
- Record live-run intent through command manifests first. The script refuses live
  execution unless the caller passes `--allow-live` and a positive
  `--max-budget-usd`.
- The harness adapter runs in dry-run/replay in tests. Live execution can invoke
  the existing durable workflow path and then apply a generated terminal script,
  but the measurement report is based on Harbor verifier outcomes, not harness
  self-reporting.
- The external 83.4 percent GPT-5.5 plus Codex and 78.9 percent Opus 4.8 numbers
  are reference context only; they are not used as pilot baselines because this
  pilot uses a fixed 30-task subset and a different harness.

## Testing Decisions

- Use deterministic fixture rows for the reporter. This proves pass@1,
  confidence interval, pass@5, pass^5, delta, and noise-floor classification
  without live calls.
- Use a fake Harbor environment with an `exec` method to exercise the agent
  adapter boundary and context metadata.
- Use script tests in dry-run mode to prove no Harbor process launches without
  explicit live authorization and budget.
- Compile the new modules and run focused pytest for the Terminal-Bench slice.

## Out Of Scope

- Running the full 89-task benchmark before the pilot has a greater-than-three-
  point signal.
- Reproducing OpenAI's exact Codex harness or Terminus-2 internals.
- Changing supervisor defaults, reviewer panel behavior, state schema, or
  agentic lead policy.
- The SWE-bench Pro arm.
