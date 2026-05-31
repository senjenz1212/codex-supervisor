# Agentic Lead Executor Wiring TDD Plan

## Public Boundary

Use `codex_supervisor_mcp`, `dual_agent_runner`, `dynamic_workflow_receipts`, `supervisor.agentic_workers`, and `codex_supervisor_workflow_cli`. The first proof must exercise `run_dual_agent_workflow`, because the operator-facing promise is about supervisor-owned workflow execution rather than a helper-only function.

## Test Cases

### test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts

Maps to: ISS-1, P1, P2, P3
RED: Run a required agentic workflow with a fake roster planner and a fake fan-out runner; assert the workflow fails because the runner is never called.
GREEN: Invoke the supervisor-owned fan-out producer, append receipts, re-run the existing P13 verifier, and let the workflow advance when runtime-native receipts verify. Assert the accepted proof is reported through P13 details and no second evidence-grade verifier/result object appears.

### test_run_dual_agent_workflow_required_policy_still_blocks_without_executor_receipts

Maps to: ISS-1, P1, P3
RED: Run a required agentic workflow without a valid roster or executor receipts and assert no synthesis `/lead` call happens.
GREEN: Preserve the existing workflow-start block when producer-created receipts are absent or invalid.

### test_agentic_roster_validation_rejects_writable_or_missing_required_roles

Maps to: ISS-2, P2
RED: Feed a roster with writable permission mode or missing required role and assert the supervisor refuses to spawn workers.
GREEN: Add validation for role coverage, max count, timeout, budget, and read-only permission. Writable permission modes are rejected, not silently coerced.

### test_agentic_worker_timeout_cleanup_runs_after_fanout_timeout

Maps to: ISS-2, ISS-5, P2, P6
RED: Simulate a timed-out worker and assert cleanup is not called.
GREEN: Wire `cleanup_orphaned_agentic_workers` into the producer path with injected liveness and termination hooks for tests.

### test_solo_exception_only_applies_to_artifact_only_gates

Maps to: ISS-3, P4
RED: Verify a required solo lead receipt with `solo_exception_for_artifact_only_gates=True` on `execution` and observe it incorrectly passes.
GREEN: Pass gate identity into policy evaluation and allow the exception only on artifact-only gates such as planning review.

### test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked

Maps to: ISS-3, P3
RED: Feed a dynamic subagent receipt that declares `runtime_native` but points transcript/output refs outside `.handoff/agentic-workers/`; assert the current verifier can be tricked or lacks this explicit regression.
GREEN: Confirm the supervisor derives the grade from replay-verified supervisor-owned worker refs, ignores the declared grade, downgrades the receipt, and blocks when `required_evidence_grade=runtime_native`.

### test_docs_dual_agent_refs_do_not_count_as_runtime_native_worker_evidence

Maps to: ISS-3, P3
RED: Create hash-valid subagent transcript/output refs under `docs/dual-agent/` and assert they are incorrectly graded the same as `.handoff/agentic-workers/` worker outputs.
GREEN: Restrict runtime-native subagent worker evidence to supervisor-owned worker/job provenance such as `.handoff/agentic-workers/` and `.handoff/workflow-jobs/`; keep `docs/dual-agent/` for readable manifests/replay artifacts without letting lead-authored readable artifacts satisfy worker runtime evidence.

### test_submit_workflow_job_payload_round_trips_agentic_policy_fields

Maps to: ISS-4, P5
REGRESSION: Submit a workflow job payload with all agentic policy fields and assert the durable request and workflow kwargs preserve every field. This is a characterization guard because the direct submit path is already partially wired and should not be described as fake RED.
GREEN: Preserve every policy field through `WORKFLOW_KEYS`, submit job request writing, poll/reload, and CLI payload conversion without changing agentic defaults.

## RED/GREEN Plan

RED: Add the workflow-level required-policy fan-out test with injected fake planner and fake executor.
GREEN: Add the smallest producer integration that plans a roster, runs the executor, appends receipts, and reuses existing P13 validation.

RED: Add malformed roster, missing role, and writable permission tests.
GREEN: Add roster parsing and validation with writable-mode rejection and bounded timeout/budget.

RED: Add over-budget and over-timeout roster specs plus a timed-out worker cleanup case.
GREEN: Reject over-limit specs before launch and call orphan cleanup on timeout/dead-worker paths while preserving failed-worker durable refs.

RED: Add the solo-exception gate identity regression.
GREEN: Thread the gate name through receipt verification and restrict the exception to artifact-only gates.

RED: Add a non-supervisor `runtime_native` declaration case that should be downgraded and blocked.
GREEN: Keep evidence grade supervisor-derived by replaying refs through existing P13 and ignoring lead-declared grades.

RED: Add a `docs/dual-agent/` subagent transcript/output case that currently over-credits readable artifacts as runtime-native worker evidence.
GREEN: Narrow runtime-native worker evidence to supervisor-owned worker/job refs while preserving readable docs artifacts as audit projections.

REGRESSION: Add a submit/poll payload round-trip characterization test for policy fields.
GREEN: Preserve policy fields in the detached workflow request, poll/reload path, and CLI conversion path.
