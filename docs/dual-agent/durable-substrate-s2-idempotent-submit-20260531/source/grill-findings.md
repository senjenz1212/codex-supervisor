# PRD Grill Findings

## Reviewed Surfaces

- `docs/testing/public-boundaries.md`
- `docs/testing/forbidden-outcomes.md`
- `supervisor/state.py` detached workflow job schema and job persistence methods
- `mcp_tools/codex_supervisor_stdio.py` detached submit/poll tool boundary
- Existing detached job tests in `tests/test_dual_agent_workflow_driver.py`

## Findings

### Finding G1: Dedup Must Happen Before Worker Launch

- Risk: Checking for existing jobs after `subprocess.Popen` would still launch a
  duplicate worker.
- Resolution: The PRD requires an atomic reserve/reattach step before the worker
  process is spawned.
Status: resolved

### Finding G2: Legacy No-Token Callers Need Stable Derived Keys

- Risk: Making `client_token` mandatory would break existing callers and tests.
- Resolution: The PRD requires optional `client_token`; absent tokens derive from
  `run_id` plus a canonical request hash.
Status: resolved

### Finding G3: Different Logical Submits Must Not Collapse

- Risk: Deduping only by `run_id` could collapse distinct detached jobs for the
  same run.
- Resolution: Derived keys include the canonicalized request payload, and
  explicit tokens remain caller-controlled.
Status: resolved

### Finding G4: Append Idempotency Is Too Broad For This Slice

- Risk: Adding event append deduplication here would blur S2 with S3b/S5.
- Resolution: Keep S2 to detached submit idempotency and record append
  idempotency as an open question.
Status: resolved
