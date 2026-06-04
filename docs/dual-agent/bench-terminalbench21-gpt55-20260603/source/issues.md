# Issues: Terminal-Bench 2.1 Harness Benchmark Adapter

## Slice 1 - Harbor agent adapter boundary

Priority: P1

Scope: Add `supervisor/terminal_bench_harbor_agent.py` with a Harbor-compatible
agent class, optional Harbor imports, dry-run support, model validation, command
transcript output, and context metadata. It must accept `model_name="gpt-5.5"`
and avoid mutating supervisor config.

PRD promise: P1, P5

Acceptance criteria:
- [ ] The class import path is `supervisor.terminal_bench_harbor_agent:CodexSupervisorTerminalBenchAgent`.
- [ ] A fake Harbor environment test exercises `setup` and `run`.
- [ ] GPT-5.5 is preserved in metadata and logs.
- [ ] Unit tests pass without Harbor installed.

## Slice 2 - Fixed pilot sample manifest

Priority: P1

Scope: Add a schema-versioned Terminal-Bench pilot sample fixture under
`tests/fixtures/terminal_bench/` with dataset id, seed, k, model, and 30 task
ids derived from the 89-task TB 2.1 dataset probe.

PRD promise: P2

Acceptance criteria:
- [ ] Loader rejects missing or duplicate task ids.
- [ ] Loader returns the 30 task ids in stable order.
- [ ] Manifest records seed `20260603`, dataset `terminal-bench/terminal-bench-2-1`, and k=5.

## Slice 3 - Report-only Terminal-Bench metrics

Priority: P1

Scope: Add `supervisor/terminal_bench_eval.py` with trial-result normalization,
per-arm pass@1 mean and 95 percent confidence intervals, pass@5, pass^5,
harness-minus-baseline delta and confidence interval, and the three-point
noise-floor verdict.

PRD promise: P3, P5

Acceptance criteria:
- [ ] Reporter consumes deterministic fixture trial results with no live calls.
- [ ] Report contains per-arm metrics, per-task rows, delta, noise-floor verdict, and external references.
- [ ] Report has `default_change_allowed=False` and `policy_mutated=False`.
- [ ] Stable report hash is reproducible from fixed input.

## Slice 4 - Live/dry-run pilot script

Priority: P2

Scope: Add `scripts/run_terminal_bench_pilot.py` to produce dry-run Harbor
commands for Terminus-2 and the harness adapter, optionally launch them only with
`--allow-live` and a positive `--max-budget-usd`, and write a replayable report
from result JSON when supplied.

PRD promise: P4, P5

Acceptance criteria:
- [ ] Dry-run mode prints or writes baseline and harness Harbor commands without launching.
- [ ] Live mode refuses to start without a positive budget cap.
- [ ] Command plan includes fixed task ids, k=5, model GPT-5.5, output dirs, and checkpoint/resume locations.
- [ ] Script report mode reuses the same reporter and keeps policy fields report-only.

## Slice 5 - Verification and artifacts

Priority: P2

Scope: Add focused tests, a deterministic pilot fixture report, test evidence,
and the supervised-flow artifacts.

PRD promise: P1, P2, P3, P4, P5

Acceptance criteria:
- [ ] Focused pytest passes.
- [ ] `py_compile` passes for new modules and script.
- [ ] `git diff --check` passes.
- [ ] Supervised workflow accepts through outcome review.
