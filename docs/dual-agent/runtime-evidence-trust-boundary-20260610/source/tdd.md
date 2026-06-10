# TDD Plan

## test_execution_gate_rejects_fabricated_runtime_receipts_for_missing_file

Maps to: Slice 1, P1

Red: A workflow run accepts caller-supplied `git_diff` and `test` receipts stamped as supervisor/runtime-native for a missing file.

Green: The receipts are downgraded, `receipt_provenance_downgraded` events are present, and P11 blocks because supervisor runtime evidence does not support the claim.

## test_submit_dual_agent_workflow_job_sanitizes_forged_runtime_receipts

Maps to: Slice 1, P1

Red: A durable `submit_dual_agent_workflow_job` request persists caller-supplied supervisor/runtime-native receipts into `request_payload_json` before sanitization.

Green: The stored durable request contains downgraded caller/self-reported receipts and emits a `receipt_provenance_downgraded` event with `scope=submit:caller_tool_receipts`.

## test_verify_helpers_do_not_trust_stamped_runtime_native_receipts

Maps to: Slice 1, P1, P2

Red: Direct helper calls accept forged runtime-native receipt dictionaries by string comparison.

Green: The helper-normalized receipts are downgraded to caller/self-reported evidence unless their ids are in the current trusted runtime receipt set.

## test_verify_helpers_do_not_trust_stale_runtime_receipts_from_other_gate

Maps to: Slice 2, P2

Red: A marker-shaped runtime receipt from an earlier gate satisfies the current gate because it still says `source=supervisor` and `evidence_grade=runtime_native`.

Green: Verification only trusts receipt ids from the current runtime-evidence invocation; stale runtime receipts are downgraded and cannot satisfy claim or deliverable checks.

## test_execution_gate_accepts_supervisor_runtime_native_receipts

Maps to: Slice 2, P2

Red: The hardening downgrades honest receipts produced by `collect_runtime_evidence`, blocking a valid execution gate.

Green: Current in-process runtime receipt ids preserve supervisor/runtime-native authority and the honest execution workflow remains accepted.

## test_runtime_receipts_replace_same_id_forged_caller_receipts

Maps to: Slice 2, P2

Red: A caller-supplied forged receipt with the same id as a collector receipt shadows the collector result during receipt merge.

Green: Runtime collector receipts replace same-id caller inputs, preserve supervisor/runtime-native authority, and the forged file is absent from trusted changed files.

## test_declared_python_c_command_is_rejected_not_executed

Maps to: Slice 3, P3

Red: A declared `python -c` command executes in the validation worktree and can create a marker file.

Green: The command is rejected before subprocess execution, the marker file is absent, and the receipt contains `runtime_test_command_rejected`.

## test_declared_make_test_stays_allowlisted_argv_command

Maps to: Slice 3, P3

Red: A declared `make test` command is normalized as a pytest label, making the low-level make allowlist unreachable from public runtime evidence collection.

Green: `make test` is preserved as a command, executed as `["make", "test"]` with `shell=False`, and remains subject to the allowlist.

## test_allowlisted_pytest_command_runs_and_reports_pass_fail

Maps to: Slice 3, P3, P5

Red: Pytest commands are either rejected with the unsafe commands or their real pass/fail result is ignored.

Green: Allowlisted pytest runs as argv, a passing test reports passed, and a failing test reports failed with a nonzero return code.

## test_runtime_test_subprocess_env_drops_secret_keys

Maps to: Slice 4, P4

Red: Parent env keys such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `SECRET_TOKEN` are visible to the validation subprocess.

Green: The runner receives a minimal env without those keys and with explicit Python isolation controls.

## test_runtime_test_environment_unavailable_is_red

Maps to: Slice 4, P5

Red: Missing pytest or missing executable is treated as an ordinary failed command without a clear hard-failure reason, or is ignored.

Green: The receipt contains `runtime_test_environment_unavailable` and the runtime evidence probe is red.
