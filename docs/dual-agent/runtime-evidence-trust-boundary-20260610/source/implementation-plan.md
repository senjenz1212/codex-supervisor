# Implementation Plan

## Files / Modules To Touch

- `supervisor/receipt_provenance.py`
- `supervisor/runtime_evidence.py`
- `supervisor/dual_agent_workflow.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `supervisor/state.py`
- `supervisor/postgres_state.py`
- `tests/test_runtime_evidence.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`

## Steps

1. Add provenance helpers that mark collector-created runtime evidence and downgrade caller-stamped supervisor/runtime-native claims.
2. Sanitize caller receipts in MCP workflow entry points and helper normalization, then emit downgrade ledger events.
3. Pass current runtime receipt ids from the collector into claim and deliverable verification for execution and outcome gates.
4. Replace shell test execution with argv allowlist parsing and a minimal scrubbed validation environment.
5. Add failure classification for rejected commands and unavailable pytest environments.
6. Update tests and fixtures so honest acceptance paths use allowlisted pytest or avoid claiming tests they do not execute.

## Risks

- Receipt deduplication could keep a forged same-id caller receipt ahead of the collector's receipt, so runtime receipts must override same-id caller inputs.
- Previous-gate runtime receipts could accidentally satisfy a later gate, so verification must receive only current-gate trusted ids.
- Tight command allowlists can break tests that were using `python -c` as a convenience fixture, so fixtures must be changed without weakening production checks.
- Environment scrubbing can hide dependencies needed by pytest, so the validation interpreter and PATH must be explicit.

## Traceability

- P1 is covered by `test_execution_gate_rejects_fabricated_runtime_receipts_for_missing_file`, `test_submit_dual_agent_workflow_job_sanitizes_forged_runtime_receipts`, and `test_verify_helpers_do_not_trust_stamped_runtime_native_receipts`.
- P2 is covered by `test_verify_helpers_do_not_trust_stale_runtime_receipts_from_other_gate`, `test_execution_gate_accepts_supervisor_runtime_native_receipts`, and `test_runtime_receipts_replace_same_id_forged_caller_receipts`.
- P3 is covered by `test_declared_python_c_command_is_rejected_not_executed`, `test_declared_make_test_stays_allowlisted_argv_command`, and `test_allowlisted_pytest_command_runs_and_reports_pass_fail`.
- P4 is covered by `test_runtime_test_subprocess_env_drops_secret_keys`.
- P5 is covered by `test_runtime_test_environment_unavailable_is_red` and `test_allowlisted_pytest_command_runs_and_reports_pass_fail`.
