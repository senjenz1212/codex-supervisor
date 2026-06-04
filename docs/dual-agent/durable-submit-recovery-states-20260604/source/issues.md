# Issues

## Slice ISS-1: Schema and State API for Recovery Phases

Type: AFK
Priority: P0
Estimate: M
Scope: Add `recovery_point`, `request_payload_json`, and `config_path` to
`dual_agent_workflow_jobs`; add active-token uniqueness for non-terminal jobs;
and expose state helpers that reserve, update, and complete jobs without
depending on Python-only locks.
PRD promise: P2, P3, P4
First public-boundary RED test: `State.reserve_dual_agent_workflow_job` against
an SQLite database with a multiprocessing race.

Acceptance Criteria:
- [ ] Direct duplicate active-token insert raises `sqlite3.IntegrityError`.
- [ ] Eight concurrent process reservations produce one active job row.
- [ ] Terminal completion sets `recovery_point=terminal`.
- [ ] Existing databases migrate forward with recovery columns and active-token index.

## Slice ISS-2: Pure Submit Boundary

Type: AFK
Priority: P0
Estimate: M
Scope: Change `submit_dual_agent_workflow_job` so it only reserves the durable
job row and returns metadata. It must not write request files or call
`subprocess.Popen`.
PRD promise: P1, P4
First public-boundary RED test: MCP tool `submit_dual_agent_workflow_job` with
`subprocess.Popen` patched to record or fail if called.

Acceptance Criteria:
- [ ] Submit returns `status=submitted`, `recovery_point=reserved`, and a `job_id`.
- [ ] Submit does not create `request.json`.
- [ ] Submit does not call `subprocess.Popen`.
- [ ] Duplicate submit with the same token reattaches to the same job.

## Slice ISS-3: Poll-Side Recovery Driver

Type: AFK
Priority: P0
Estimate: M
Scope: Make `poll_dual_agent_workflow_job` drive `reserved ->
request_written -> spawned`, then preserve existing terminal result recovery.
Polling a stranded reservation must either spawn it or record a terminal
failure.
PRD promise: P3, P4, P5
First public-boundary RED test: MCP tool `poll_dual_agent_workflow_job` on a
reserved job with durable payload and `pid=NULL`.

Acceptance Criteria:
- [ ] Poll of a reserved job writes request JSON and records `request_written`.
- [ ] Poll of a request-written job spawns once and records `spawned` with pid.
- [ ] Spawn failure records terminal failed outcome and `recovery_point=terminal`.
- [ ] Terminal jobs replay stored outcomes without spawning.

## Slice ISS-4: Replay and Regression Evidence

Type: AFK
Priority: P1
Estimate: S
Scope: Update detached workflow tests and migration tests so the new contract is
covered without weakening existing catch-up, terminal-outcome, reviewer, or gate
behavior.
PRD promise: P1, P2, P3, P4, P5
First public-boundary RED test: focused workflow-driver and migration pytest
selection before the full suite.

Acceptance Criteria:
- [ ] Focused workflow and migration tests pass.
- [ ] Full test suite passes.
- [ ] `git diff --check` passes.
- [ ] The supervised workflow exports transcript, replay, and outcome artifacts.
