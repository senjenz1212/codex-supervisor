# TDD Plan

Task id: `pro-oracle-gold-proof-20260626`

## One RED Then One Minimal GREEN

### RED 1: Gold receipt fixture proves oracle-good shape

Public boundary

`tests/test_pro_oracle_gold_proof.py::test_gold_run_receipt_is_oracle_good` reads the committed real receipt artifact produced by `run_swe_bench_pro_oracle(...)` on the linux/amd64 VM.

Expected failure before GREEN

The packet has no committed oracle-good receipt, so the test fails because the fixture is missing or does not show `fail_to_pass_status="pass"`, `pass_to_pass_status="pass"`, raw `patch_apply.json.patch_applied is true`, `test_command_return_code=0`, non-empty required buckets, non-empty selected tests, and non-empty parsed tests.

Minimal GREEN

Run one real gold Pro instance on the VM with a deterministic runner/runbook, copy the receipt, raw workspace receipts, VM preflight, and compact parsed-test artifact into `docs/dual-agent/pro-oracle-gold-proof-20260626/artifacts/`, and make the test assert the receipt's public outcome shape.

### RED 2: Empty parser output is unavailable, not pass or ordinary fail

Public boundary

`tests/test_pro_oracle_gold_proof.py::test_empty_parser_output_is_unavailable_not_pass` calls `run_swe_bench_pro_oracle(...)` with Docker faked below the adapter boundary.

Expected failure before GREEN

The adapter accepts `{"tests":[]}` as a parsed output, returns ordinary fail statuses, and does not mark `oracle_unavailable` for a crash-shaped nonzero test command.

Minimal GREEN

Teach the Pro parser/subset path to classify empty parser output with nonzero test command return code as unavailable with a specific reason, while preserving non-empty oracle-good and oracle-bad classification. Use parsed test count, not only the passed-test set.

## Focused Commands

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_pro_oracle_gold_proof.py tests/test_swe_bench_pro_oracle.py -p no:cacheprovider
```

## Live Execution Command Shape

The live command is not a pytest test. It runs on the VM and writes the artifact consumed by RED 1:

```sh
python -m scripts.pro_oracle_gold_proof_runner
```

If no script is introduced, use an equivalent one-off Python invocation that imports `run_swe_bench_pro_oracle(...)`, loads the selected real dataset row, validates non-empty required fields, and writes the receipt under this packet's `artifacts/` directory.

## Forbidden Test Shapes

- Do not run real Docker from pytest.
- Do not mock `run_swe_bench_pro_oracle(...)` itself.
- Do not assert only implementation details while bypassing the adapter's result contract.
- Do not let empty parser output pass the guard.
