# Tri-Agent Findings

Task id: `powered-factorial-runner-20260626`

## Agent A: Production Reachability

Agent: `019f0d69-e2d0-75f3-9a60-7aeadbe6c953`

Verdict: accept as a planning/TDD packet, with strict implementation condition.

Key findings:

- The production CLI previously reached official replay, all-arms diagnostic, live, and replay paths, but not `run_powered_factorial_mergeability_evaluation(...)`.
- The all-arms diagnostic is the wrong path for this task because it remains diagnostic/report-only and keeps authority flags false.
- The implementation must expose a distinct `--powered-factorial` path and reject solutions that merely decorate `official_all_arms_diagnostic_report.json`.

File refs recorded by validator:

- `pyproject.toml:30`
- `supervisor/swe_bench_mergeability_cli.py:32`
- `supervisor/swe_bench_mergeability_cli.py:223`
- `supervisor/swe_bench_mergeability.py:2796`
- `supervisor/swe_bench_mergeability.py:3235`
- `supervisor/swe_bench_mergeability.py:3354`
- `supervisor/mergeability_bench.py:1792`

Resolution:

- Added a dedicated CLI mode, `--powered-factorial`, and a separate runner, `swebench_mergeability_powered_factorial_runner(...)`.
- Kept all-arms diagnostic behavior separate and unchanged.

## Agent B: Pro Corpus Adaptation

Agent: `019f0d69-ff94-7883-ac97-d8f36e2d613e`

Verdict: revise.

Key findings:

- The packet named the right boundary, but the implementation had to force the Pro predictions JSONL to be the authority for oracle labels, hashes, baseline receipts, and arm decisions.
- Missing arm maps could otherwise fall back to evaluator defaults.
- Precomputed Pro evidence must be mandatory for the Pro-powered runner, not an accidental local fixture path.

File refs recorded by validator:

- `docs/dual-agent/powered-factorial-runner-20260626/prd.md:23`
- `docs/dual-agent/powered-factorial-runner-20260626/prd.md:40`
- `supervisor/swe_bench_mergeability.py:4443`
- `supervisor/swe_bench_mergeability.py:4456`
- `supervisor/swe_bench_mergeability.py:4472`
- `supervisor/mergeability_bench.py:1822`
- `supervisor/mergeability_bench.py:1856`
- `supervisor/mergeability_bench.py:1886`
- `supervisor/mergeability_bench.py:1932`
- `docs/dual-agent/powered-factorial-runner-20260626/issues.md:61`
- `supervisor/mergeability_bench.py:1904`
- `supervisor/mergeability_bench.py:5078`
- `supervisor/mergeability_bench.py:4932`
- `supervisor/mergeability_bench.py:4998`
- `supervisor/mergeability_bench.py:5042`
- `tests/test_powered_factorial_runner.py:7`
- `supervisor/swe_bench_mergeability_cli.py:36`
- `supervisor/swe_bench_mergeability_cli.py:278`

Resolution:

- Extended `_load_official_predictions(...)` to preserve powered arm decisions and reviewer panel rows.
- Made the powered runner validate oracle labels, candidate hashes, provenance, trusted baseline receipts, and every required arm decision before invoking the evaluator.
- Added regression tests for missing `full_supervisor_stack_decision` and hash-mismatched baseline receipts.

## Agent C: Power Contract

Agent: `019f0d6a-1e51-75e2-9512-0a93ea445cab`

Verdict: unavailable.

Outcome:

- The agent did not return a verdict after two long waits and an interrupt requesting a current verdict.
- It was closed with previous status `running`.

Resolution:

- Treated this as a validation gap, not an acceptance.
- Covered the intended Agent C risk locally with focused tests that assert `evidence_conversion_power_contract.status` is `qualified` only when sample-size and paired-power gates pass, and `underpowered` with explicit reasons otherwise.

## Fold-Back Summary

- Production reachability: implemented via a distinct CLI mode and runner.
- Pro corpus authority: implemented by fail-closed validation before evaluator invocation.
- Power contract: implemented as report-only evidence with threshold-controlled status and authority flags held false.

