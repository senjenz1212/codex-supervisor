# Implementation Plan: OBS-001

## Sequence

1. Register the workflow run and target-agent session when submission is
   reserved; preserve the first binding on idempotent reattach.
2. Persist a session-keyed join record containing workflow, task, target, and
   scope identity.
3. Normalize nested Claude, Codex, and OpenCode rollout events to one lifecycle
   taxonomy while retaining the raw payload.
4. Store events under the workflow run ID and require every retained event to
   join to one task, workflow, and target session.
5. Mark normalized terminal events exactly once and queue evaluation.
6. Evaluate semantic drift independently of path-scope violations.

## Traceability

- Captured event normalization and terminal state ->
  `tests/test_obs_001_observability.py`
- Run registration API -> `tests/test_run_registration_api.py`
- Drift independence -> `tests/test_drift_detector_rewire.py`

## Exit Boundary

OBS-001 repairs new-run ingestion. Historical stale-run backfill remains a
separate operational migration and is not silently inferred from these tests.
