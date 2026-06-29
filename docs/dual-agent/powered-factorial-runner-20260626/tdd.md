# TDD Plan

Task id: `powered-factorial-runner-20260626`

## Public Boundary

The first RED test is the CLI boundary because the requested user-visible behavior is a production runner, not another helper. The CLI test must prove threshold flags exist and flow into a report. The runner test then proves the Pro corpus is consumed and adapted into `run_powered_factorial_mergeability_evaluation(...)`.

## One RED Then Minimal GREEN

RED

Add `tests/test_powered_factorial_runner.py::test_cli_exposes_power_thresholds`. It invokes `supervisor.swe_bench_mergeability_cli.main(...)` with `--powered-factorial`, a fixture Pro predictions JSONL, `--min-good`, `--min-bad`, `--min-discordant`, and `--alpha`. Before implementation, argparse rejects the new mode or the report lacks the requested thresholds.

Minimal GREEN

Add the CLI mode and a minimal runner that loads the Pro corpus, calls `run_powered_factorial_mergeability_evaluation(...)` with the passed thresholds, writes a report, and prints summary JSON.

## Next Cycles

### Cycle 2: Consume Pro Corpus, Not Local Fixture

RED: `tests/test_powered_factorial_runner.py::test_factorial_runner_consumes_pro_corpus` writes Pro-labeled JSONL rows with explicit arm decisions and trusted baseline receipts, then asserts the report records the predictions path, candidate hashes, and Pro-derived candidate count.

GREEN: Add the Pro-to-factorial adapter and precomputed evaluator inputs.

### Cycle 3: Qualified Only When Powered

RED: `tests/test_powered_factorial_runner.py::test_power_contract_qualified_only_when_powered` builds a fixture that meets custom small thresholds and has significant full-stack discordance, then asserts `evidence_conversion_power_contract.status == "qualified"`.

GREEN: Derive the contract from sample size and paired power.

### Cycle 4: Underpowered Reports Stay Blocked

RED: `tests/test_powered_factorial_runner.py::test_underpowered_reports_not_qualified` uses the same corpus with higher thresholds and asserts the contract is underpowered with reasons.

GREEN: Preserve threshold reasons and fail closed.

## Refactor Check

- Keep the runner as a deep module: one public function plus CLI.
- Do not fork McNemar or Wilson math.
- Do not alter all-arms diagnostic authority.
- Do not fake live Pro solver or Docker execution.

## Ledger Record

```jsonl
{"stage":"tdd_review","task_id":"powered-factorial-runner-20260626","status":"accepted","first_red":"tests/test_powered_factorial_runner.py::test_cli_exposes_power_thresholds","evidence":["docs/dual-agent/powered-factorial-runner-20260626/tdd.md","docs/dual-agent/powered-factorial-runner-20260626/grill-findings-tdd.md","docs/dual-agent/powered-factorial-runner-20260626/translation-audit.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
