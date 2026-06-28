# Issues

Task id: `powered-factorial-runner-20260626`

## 1. Add the explicit powered CLI mode

Promise: P1, P2

Public boundary: `supervisor.swe_bench_mergeability_cli.main(...)`

Chosen seam: `--powered-factorial` plus `--predictions`, `--output-dir`, and threshold flags.

First RED test: `tests/test_powered_factorial_runner.py::test_cli_exposes_power_thresholds`

Allowed: CLI invokes the powered Pro runner and prints a summary containing contract status and thresholds.

Forbidden: Hiding powered execution behind official all-arms diagnostic flags.

## 2. Preserve Pro corpus evidence through the loader

Promise: P1

Public boundary: `_load_official_predictions(...)`

Chosen seam: JSONL rows with oracle labels, hashes, baseline receipts, and explicit arm decision fields.

First RED test: `tests/test_powered_factorial_runner.py::test_factorial_runner_consumes_pro_corpus`

Allowed: Loader preserves fields needed by the powered runner.

Forbidden: Dropping arm decisions or provenance before the runner sees them.

## 3. Build a Pro-to-factorial adapter

Promise: P1

Public boundary: new Pro-powered runner function.

Chosen seam: Adapter output passed into `run_powered_factorial_mergeability_evaluation(...)`.

First RED test: `tests/test_powered_factorial_runner.py::test_factorial_runner_consumes_pro_corpus`

Allowed: Pro candidates become precomputed evaluator rows with Pro artifact hashes.

Forbidden: Running the local fixture corpus or recomputing labels from local tests.

## 4. Validate trusted baseline receipts

Promise: P1

Public boundary: powered runner report rows.

Chosen seam: `_resolve_powered_baseline_decision(...)` receives the Pro `candidate_artifact_hash`.

First RED test: `tests/test_powered_factorial_runner.py::test_factorial_runner_consumes_pro_corpus`

Allowed: Matching trusted receipts make baseline available.

Forbidden: Legacy bools, untrusted sources, or hash mismatches becoming available.

## 5. Require explicit full-stack arm decisions

Promise: P1

Public boundary: powered runner invocation.

Chosen seam: corpus row fields for `full_supervisor_stack_decision` and sibling arm decisions.

First RED test: `tests/test_powered_factorial_runner.py::test_underpowered_reports_not_qualified`

Allowed: Complete decision rows power the evaluator.

Forbidden: Missing rows falling back to public-review defaults.

## 6. Thread power thresholds

Promise: P2

Public boundary: CLI threshold flags.

Chosen seam: `powered_thresholds` passed to the evaluator.

First RED test: `tests/test_powered_factorial_runner.py::test_cli_exposes_power_thresholds`

Allowed: Custom `--min-good`, `--min-bad`, `--min-discordant`, and `--alpha` appear in the report contract.

Forbidden: Hard-coded floors that the operator cannot override.

## 7. Emit the evidence conversion power contract

Promise: P2

Public boundary: powered report JSON.

Chosen seam: contract derived from sample-size sufficiency plus paired-power sufficiency.

First RED test: `tests/test_powered_factorial_runner.py::test_power_contract_qualified_only_when_powered`

Allowed: Contract reports `qualified` only when all power gates pass.

Forbidden: Contract missing, or qualified without McNemar significance.

## 8. Keep authority flags false

Promise: P2

Public boundary: powered report JSON.

Chosen seam: report-only flags after the evaluator returns.

First RED test: `tests/test_powered_factorial_runner.py::test_power_contract_qualified_only_when_powered`

Allowed: Contract can be qualified while mutation flags remain false.

Forbidden: `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, or `gate_advanced` becoming true.

## 9. Write replayable powered artifacts

Promise: P1, P2

Public boundary: `--output-dir` artifact set.

Chosen seam: evaluator export plus runner metadata.

First RED test: `tests/test_powered_factorial_runner.py::test_factorial_runner_consumes_pro_corpus`

Allowed: report, adapter manifest, calibration summary, trend rows, and per-task rows are written.

Forbidden: CLI success without a report file.

## 10. Block underpowered reports honestly

Promise: P2

Public boundary: `evidence_conversion_power_contract`.

Chosen seam: status and reasons from thresholds.

First RED test: `tests/test_powered_factorial_runner.py::test_underpowered_reports_not_qualified`

Allowed: Underpowered reports explain missing good, bad, discordant, or alpha gates.

Forbidden: Describing an underpowered seed corpus as benchmark-ready.

## 11. Preserve AutoResearch boundary

Promise: P1, P2

Public boundary: powered report and promotion bridge behavior.

Chosen seam: contract is readable by `benchmark_promotion.py` but no policy evolution is invoked.

First RED test: `tests/test_powered_factorial_runner.py::test_power_contract_qualified_only_when_powered`

Allowed: The report is conversion-ready only as operator-reviewed evidence.

Forbidden: Building or invoking the autonomous benchmark-to-policy bridge.

## Ledger Record

```jsonl
{"stage":"issues_review","task_id":"powered-factorial-runner-20260626","status":"accepted","evidence":["docs/dual-agent/powered-factorial-runner-20260626/issues.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
