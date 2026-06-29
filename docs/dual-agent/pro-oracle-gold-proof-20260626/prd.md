# Pro Oracle Gold Proof PRD

Task id: `pro-oracle-gold-proof-20260626`

Depends on: `swe-bench-pro-oracle-adapter-20260626`

## Problem Statement

The SWE-bench Pro path has real Docker adapter plumbing, but the current committed artifacts do not prove that the Pro oracle can return oracle-good on a gold instance. Prior Apple Silicon attempts produced empty parser output after infrastructure failure, including Redis/QEMU crashes, and later native attempts exposed a separate adapter-ordering risk: the Pro row's `before_repo_set_cmd` can reset the repository after patch application if the adapter runs it in the wrong order. Without one native-amd64 gold pass and a guard against empty parser output, downstream corpus generation can mistake environment failure for a legitimate oracle-negative candidate.

## Solution

Run one real SWE-bench Pro gold instance on the linux/amd64 VM through the existing `run_swe_bench_pro_oracle` Docker adapter, commit the resulting receipt under this packet's `artifacts/` directory, and add a focused unit guard so empty parser output with a nonzero test command is unavailable, not pass or ordinary fail. If the live VM run is blocked, commit an honest unavailable artifact with all authority flags false instead of fabricating an oracle-good receipt.

## PRD Promise Contracts

### P1. The Pro adapter yields oracle-good on a real gold instance.

Public boundary

`run_swe_bench_pro_oracle(...)` in `supervisor/swe_bench_official_oracle.py`, invoked for one real SWE-bench Pro instance with the dataset reference patch on the native linux/amd64 VM.

Chosen seam

The existing Pro Docker adapter selected by `supervisor.swe_bench_official_oracle:run_swe_bench_pro_oracle`, `official_docker_or_equivalent`, and either the vendored Pro run scripts or `SWEBENCH_PRO_ORACLE_SCRIPTS_DIR`.

Allowed outcomes

- A committed real receipt with `fail_to_pass_status="pass"`, `pass_to_pass_status="pass"`, raw `patch_apply.json` proving `patch_applied=true`, `test_command_return_code=0`, non-empty `fail_to_pass`, non-empty `pass_to_pass`, non-empty selected tests, and non-empty parsed tests.
- A committed blocked/unavailable artifact when the VM, Docker image, script set, or selected gold instance cannot produce the receipt, with exact blocker text and all authority flags false.
- The adapter may fix discovered execution-order bugs needed for the real gold patch to survive until test execution.

Forbidden outcomes

- Empty parser output counted as oracle-good.
- Claiming oracle-good when `test_command_return_code != 0`, parsed tests are empty, required buckets are empty, selected tests are empty, or raw patch-apply proof is missing/malformed/false.
- Mocking the real Docker run or using fixture output as the execution artifact.
- Setting any authority flag true.

### P2. Honest guard against the environment-crash false-fail.

Public boundary

The Pro adapter's parser/subset classification path that turns `output.json`, `test_command.json`, and selected test buckets into normalized oracle statuses.

Chosen seam

A fixture-driven call through `run_swe_bench_pro_oracle(...)` with Docker faked below the adapter boundary: the fake runner writes captured parser/test-command shapes, while the adapter's real parsing, subset check, receipt writing, and unavailable classification remain in the path.

Allowed outcomes

- Captured parser output with real passed tests can support `pass`.
- Empty `{"tests":[]}` with nonzero test command return code returns `oracle_unavailable` with a real reason.
- The guard test verifies parsed tests are non-empty before any oracle-good claim.

Forbidden outcomes

- A guard that passes on empty parser output.
- Treating infrastructure crash output as an ordinary oracle-bad candidate.
- Faking adapter status above the public boundary.
- Hiding parser/test-command receipts in uncommitted scratch state.

## User Stories

1. As the benchmark operator, I want one gold SWE-bench Pro instance to pass on native amd64, so that the Pro oracle is empirically proven before candidate generation.
2. As the benchmark operator, I want the receipt committed with command, Docker, patch, parser, test-command, and output evidence, so that later slices can audit what actually ran.
3. As the benchmark operator, I want empty parser output to be unavailable, so that Redis/QEMU/disk failures cannot masquerade as oracle-negative candidates.
4. As the benchmark operator, I want adapter execution-order bugs fixed before the proof, so that the dataset gold patch is actually present when tests run.
5. As the benchmark operator, I want authority flags to remain false, so that this proof does not become a benchmark, powered metric, or policy mutation claim.

## Implementation Decisions

- Keep `run_swe_bench_pro_oracle(...)` as the public execution interface.
- Use the existing vendored scripts for one of the three vendored instances unless the VM run proves a different upstream script is required.
- Use a deterministic runner or exact runbook that pins the instance id, loads the real dataset row, maps dataset `patch` into the adapter's `model_patch`, validates required row fields, and writes artifacts directly under this packet.
- Treat real Docker execution as outcome evidence, not as a pytest unit test.
- Add fixture tests below the Docker boundary only after the PRD, grill, issue, and TDD gates are accepted.
- Preserve oracle isolation: no reviewer, solver, candidate generation, policy bridge, or powered stats work is part of this slice.

## Testing Decisions

- The first RED test is `tests/test_pro_oracle_gold_proof.py::test_gold_run_receipt_is_oracle_good`, using the committed real receipt fixture to assert oracle-good shape and non-empty parsed tests.
- The second RED test is `tests/test_pro_oracle_gold_proof.py::test_empty_parser_output_is_unavailable_not_pass`, using a fake Docker runner below `run_swe_bench_pro_oracle(...)` to write empty parser output and nonzero `test_command.json`.
- No pytest test runs real Docker; the live VM run is captured as a committed artifact.
- Existing Pro oracle tests remain part of focused regression coverage.
- The receipt fixture test must validate the raw `patch_apply.json`, `test_command.json`, `output.json`, required buckets, selected tests, and manifest authority flags.

## Out of Scope

- Candidate generation and corpus production.
- Baseline receipt production.
- Reviewer panels and cross-family verification.
- Powered factorial statistics.
- Autonomous benchmark-to-policy bridge work.
- Any policy/config/default mutation.

## Further Notes

This slice proves only that the Pro oracle can produce one oracle-good gold result and that crash-shaped empty parser output is fail-closed. It does not prove FAR, TAR, all-arms availability, statistical power, human mergeability, or auto-evolve readiness.
