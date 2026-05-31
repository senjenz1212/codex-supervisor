# Agentic Lead Executor Wiring Issues

## Slice ISS-1: Supervisor-Owned Agentic Producer

Type: AFK
Priority: P0
Estimate: L
Scope: Connect `run_agentic_worker_fanout` into the workflow path for `agentic_lead_policy=allowed|required`, using a lead-planned roster and appending executor-produced receipts before existing P13 validation.
PRD promise: P1, P2, P3
First public-boundary RED test: `test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts`

Acceptance Criteria:
- [ ] Required agentic workflow invokes the supervisor-owned fan-out runner.
- [ ] Worker refs land under `.handoff/agentic-workers/<task>/<worker>/`.
- [ ] Existing P13 replay verification derives `runtime_native` from executor-produced refs.
- [ ] The synthesis `/lead` call receives or can read the worker receipts after the producer succeeds.
- [ ] A required workflow with no executor-produced receipts still blocks before synthesis.

## Slice ISS-2: Roster Contract And Read-Only Enforcement

Type: AFK
Priority: P0
Estimate: M
Scope: Define and validate the machine-readable worker roster contract that `/lead` returns, including role, persona id, permission mode, tool pins, scoped prompt, timeout, and budget.
PRD promise: P2, P6
First public-boundary RED test: `test_agentic_roster_validation_rejects_writable_or_missing_required_roles`

Acceptance Criteria:
- [ ] Roster parsing rejects malformed specs and missing required roles.
- [ ] Writable permission modes are rejected before subprocess launch.
- [ ] Per-agent timeout and budget are bounded by policy inputs, and over-limit specs are rejected before subprocess launch.
- [ ] `/lead` is not allowed to spawn workers directly as part of roster planning.

## Slice ISS-3: Agentic Policy Evaluation Fixes

Type: AFK
Priority: P1
Estimate: M
Scope: Make `solo_exception_for_artifact_only_gates` gate-type-aware and keep evidence grade supervisor-derived.
PRD promise: P3, P4
First public-boundary RED test: `test_solo_exception_only_applies_to_artifact_only_gates`

Acceptance Criteria:
- [ ] `verify_dynamic_workflow_receipts` passes gate identity into `_evaluate_agentic_lead_policy`.
- [ ] Solo execution is excused only on artifact-only gates.
- [ ] Execution and outcome gates block solo execution under required policy.
- [ ] Declared `runtime_native` from a non-supervisor path is still downgraded and blocked when required.
- [ ] Runtime-native acceptance is evidenced by existing P13 replay details, not a duplicate verifier result.
- [ ] Hash-valid subagent transcript/output refs under `docs/dual-agent/` do not satisfy runtime-native worker evidence unless they are backed by supervisor-owned worker provenance.

## Slice ISS-4: Workflow CLI Policy Round Trip

Type: AFK
Priority: P1
Estimate: S
Scope: Add direct submit/poll payload characterization coverage proving agentic policy fields survive workflow CLI payload conversion and cannot regress.
PRD promise: P5
First public-boundary regression test: `test_submit_workflow_job_payload_round_trips_agentic_policy_fields`

Acceptance Criteria:
- [ ] `agentic_lead_policy` survives payload conversion.
- [ ] `min_subagents`, `required_roles`, `solo_exception_for_artifact_only_gates`, and `required_evidence_grade` survive conversion.
- [ ] Detached submit/poll uses the same workflow argument surface as direct execution.
- [ ] No agentic defaults change.

## Slice ISS-5: Worker Lifecycle Bounds And Cleanup

Type: AFK
Priority: P1
Estimate: M
Scope: Enforce per-agent timeout/budget caps in roster validation and call `cleanup_orphaned_agentic_workers` when worker fan-out times out or detects dead worker processes.
PRD promise: P6
First public-boundary RED test: `test_agentic_worker_timeout_cleanup_runs_after_fanout_timeout`

Acceptance Criteria:
- [ ] Over-budget and over-timeout roster specs are rejected before subprocess launch.
- [ ] Timed-out workers produce failed receipts rather than runtime-native success evidence.
- [ ] Timeout/dead-worker paths invoke orphan cleanup with supervisor-owned worker metadata.
- [ ] Durable stdout/stderr/transcript refs remain available for failed workers.
