# Issues

Task id: `pro-oracle-gold-proof-20260626`

## 1. Select A Vendored Real Pro Gold Instance

PRD promise

P1

Public boundary

`run_swe_bench_pro_oracle(...)`.

Chosen seam

Dataset row plus vendored `run_script.sh`/`parser.py` pair.

Tracer bullet

Load or reconstruct one selected Pro row with dataset `patch`, `fail_to_pass`, `pass_to_pass`, `selected_test_files_to_run`, `before_repo_set_cmd`, `base_commit`, and `dockerhub_tag`; verify required buckets and selected tests are non-empty.

Allowed outcomes

Use a real Pro row whose script pair exists locally or through `SWEBENCH_PRO_ORACLE_SCRIPTS_DIR`.

Forbidden outcomes

Inventing a row, using fixture-only patch text, or omitting selected tests.

## 2. Prove VM Readiness Before The Run

PRD promise

P1

Public boundary

Committed execution artifact.

Chosen seam

VM preflight command output recorded beside the receipt.

Tracer bullet

Record native linux/amd64, Docker version, no QEMU platform override, and sufficient disk.

Allowed outcomes

Preflight proves native Docker can run or records the exact blocker.

Forbidden outcomes

Claiming the Apple Silicon/QEMU blocker is gone without VM evidence.

## 3. Run The Real Gold Oracle

PRD promise

P1

Public boundary

`run_swe_bench_pro_oracle(...)`.

Chosen seam

Existing Pro Docker adapter with real Docker below it.

Tracer bullet

Execute the chosen gold instance on the VM and capture the adapter result JSON plus workspace artifacts.

Allowed outcomes

`fail_to_pass_status="pass"`, `pass_to_pass_status="pass"`, `patch_applied=true`, `test_command_return_code=0`, non-empty parsed tests.

Forbidden outcomes

Using fake Docker output for the execution artifact.

## 4. Preserve The Gold Receipt As A Committed Artifact

PRD promise

P1

Public boundary

`docs/dual-agent/pro-oracle-gold-proof-20260626/artifacts/`.

Chosen seam

Receipt JSON and compact evidence manifest.

Tracer bullet

Copy the VM receipt and selected workspace receipts into this packet and commit them.

Allowed outcomes

Durable artifact includes command, hashes, output path references, parser/test-command evidence, raw patch-apply evidence, VM preflight, and authority flags false.

The manifest includes `patch_apply.json`, `test_command.json`, `output.json`, entryscript, run script, parser, patch hashes, parsed-test count, selected-test count, fail-to-pass count, pass-to-pass count, VM preflight, and raw adapter receipt.

Forbidden outcomes

Leaving proof only in `/tmp`, `.scratch`, or the VM filesystem.

## 5. Fail Closed On Empty Parser Output

PRD promise

P2

Public boundary

`run_swe_bench_pro_oracle(...)`.

Chosen seam

Fake Docker runner writes `output.json={"tests":[]}` and `test_command.json` below the adapter boundary.

Tracer bullet

First RED: `tests/test_pro_oracle_gold_proof.py::test_empty_parser_output_is_unavailable_not_pass`.

Allowed outcomes

The adapter returns `oracle_unavailable` with a real unavailable reason.

Forbidden outcomes

Returning pass or ordinary fail for crash-shaped empty parser output.

## 6. Assert Gold Receipt Shape

PRD promise

P1

Public boundary

Committed receipt fixture read by pytest.

Chosen seam

Fixture receipt validator in `tests/test_pro_oracle_gold_proof.py`.

Tracer bullet

First RED: `tests/test_pro_oracle_gold_proof.py::test_gold_run_receipt_is_oracle_good`.

Allowed outcomes

Fixture has pass/pass, raw patch-apply proof, rc 0, non-empty required buckets, non-empty selected tests, non-empty parsed tests, and false authority flags.

Forbidden outcomes

Accepting pass/pass without non-empty parsed tests.

## 7. Keep Parser Classification Honest

PRD promise

P2

Public boundary

The adapter result contract.

Chosen seam

`_pro_passed_tests(...)` and subset status calculation behind `run_swe_bench_pro_oracle(...)`.

Tracer bullet

Minimal GREEN: reject or mark unavailable when parser output is empty and the test command failed.

Allowed outcomes

Parsed pass output still works; crash-shaped empty output is unavailable.

Forbidden outcomes

Breaking legitimate non-empty oracle-bad classification.

## 8. Fix Execution Ordering If The Gold Patch Is Reset Away

PRD promise

P1

Public boundary

`run_swe_bench_pro_oracle(...)` real VM result.

Chosen seam

`_pro_entryscript(...)` ordering.

Tracer bullet

If the VM receipt shows the solution patch is absent after `before_repo_set_cmd`, split or sanitize destructive repo-reset setup so it runs before patch application, preserve hidden-test checkout semantics, and add a focused `_pro_entryscript(...)` guard.

Allowed outcomes

Tests run against base code plus selected hidden tests plus candidate patch.

Forbidden outcomes

Applying the candidate patch before a dataset reset command that erases it.

Moving the entire `before_repo_set_cmd` wholesale in a way that drops hidden-test checkout/setup semantics.

## 9. Preserve Oracle Isolation

PRD promise

P1, P2

Public boundary

Execution artifact and test fixtures.

Chosen seam

No reviewer, solver, policy, or benchmark machinery enters this packet.

Tracer bullet

Audit changed files and artifacts before commit.

Allowed outcomes

Only oracle proof, guard tests, packet docs, and receipt artifacts change.

Forbidden outcomes

Building candidate generation, panel, powered stats, or benchmark-to-policy bridge.

## 10. Keep Authority Flags False

PRD promise

P1, P2

Public boundary

Committed artifact and ledger.

Chosen seam

Authority flag block copied into artifacts and ledger rows.

Tracer bullet

Assert every authority flag remains false in the receipt manifest.

Allowed outcomes

The proof is evidence only.

Forbidden outcomes

Any true authority flag.

## 11. Validate And Commit Before No-Mistakes

PRD promise

P1, P2

Public boundary

Committed source, tests, docs, and receipt artifact.

Chosen seam

Focused pytest plus clean git status before `no-mistakes`.

Tracer bullet

Run focused tests, commit the change, then run the no-mistakes gate.

Allowed outcomes

Committed worktree with passing focused tests and no-mistakes outcome.

Forbidden outcomes

Running no-mistakes on uncommitted source or scratch-only evidence.
