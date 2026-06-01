# Agentic Executor Wiring Inline Implementation Plan

## Files / Modules To Touch

- `supervisor/agentic_workers.py`
- `supervisor/agentic_executor.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/dynamic_workflow_receipts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `tests/test_agentic_workers.py`
- `tests/test_dynamic_workflow_receipts.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`

## Risks

- Running fan-out before the existing P13 block can accidentally invert the current fail-closed behavior if invalid rosters are treated as acceptable evidence.
- Worker subprocesses can become write-capable if permission mode is only documented rather than enforced in the roster-to-command path.
- Adding producer receipts without replaying hashes through the existing verifier could create a parallel evidence path that looks stronger than it is.
- Cursor review can block a technically correct implementation if the typed outcome contract is not preserved in rigorous review gates.
- A detached workflow round-trip test can become green-on-arrival; treat it as regression coverage unless a concrete dropped-field path is discovered.

## Traceability

- P1 -> test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts
- P1 -> test_run_dual_agent_workflow_required_policy_still_blocks_without_executor_receipts
- P1 -> test_run_dual_agent_workflow_allowed_policy_runs_producer_without_blocking_on_missing_receipts
- P3 -> test_run_dual_agent_workflow_allowed_policy_runs_producer_without_blocking_on_missing_receipts
- P2 -> test_agentic_roster_validation_rejects_writable_or_missing_required_roles
- P6 -> test_agentic_roster_validation_rejects_over_budget_or_timeout_before_launch
- P2 -> test_agentic_worker_timeout_cleanup_runs_after_fanout_timeout
- P3 -> test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts
- P3 -> test_run_dual_agent_workflow_required_policy_still_blocks_without_executor_receipts
- P3 -> test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked
- P3 -> test_docs_dual_agent_refs_do_not_count_as_runtime_native_worker_evidence
- P4 -> test_solo_exception_only_applies_to_artifact_only_gates
- P5 -> test_submit_workflow_job_payload_round_trips_agentic_policy_fields
- P6 -> test_agentic_worker_timeout_cleanup_runs_after_fanout_timeout
- P6 -> test_agentic_timeout_receipt_blocks_runtime_native_and_preserves_failed_refs
- P6 -> test_agentic_roster_validation_rejects_writable_or_missing_required_roles

## Steps

1. Add RED tests for required-policy producer invocation, allowed-policy non-blocking producer attempts, missing-receipt blocking, roster validation, over-budget/over-timeout pre-launch rejection, timed-out-worker failed receipt replay, solo-exception gate scoping, non-supervisor evidence downgrade, `docs/dual-agent/` provenance over-crediting, and timeout cleanup. Add CLI payload round-trip as regression/characterization coverage.
2. Add a roster model and parser that accepts machine-readable lead-planned worker specs and normalizes role, persona, permission mode, tool pins, scoped prompt, timeout, and budget.
3. Reject writable, over-budget, over-timeout, malformed, or role-incomplete rosters before subprocess launch.
4. Add a supervisor producer function that validates roster policy, calls `run_agentic_worker_fanout`, runs cleanup for timed-out/dead workers, and returns `dynamic_subagent_result` receipts.
5. Thread producer receipts into the existing P13 validation path without duplicating grade derivation or P14 synthesis; assert the accepted proof is reported as P13 and runtime-native worker evidence is limited to supervisor-owned worker/job refs.
6. Pass gate identity into dynamic workflow receipt policy evaluation and restrict solo exceptions to artifact-only gates.
7. Add direct workflow CLI submit/poll payload assertions for all agentic policy fields as regression coverage.
8. Run focused tests, then the full `uv run --extra dev pytest -q` suite and `git diff --check`.
