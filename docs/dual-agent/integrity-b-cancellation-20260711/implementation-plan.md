# Implementation Plan: INTEGRITY-B

## Sequence

1. Persist worker PID, process-group ID, start identity, containment ID, and
   reap timestamp before a spawned job can publish terminal evidence.
2. Launch workers in a new session and assign a containment identifier that is
   inherited by descendants.
3. On cancellation, stale lease, spawn failure, and result recovery, terminate
   the process group and any detached descendants with bounded TERM/KILL.
4. Refuse to signal a reused PID when the recorded process-start identity no
   longer matches.
5. Record worker reaping before terminal completion and freeze the process
   identity once the job is terminal.

## Traceability

- Process-group and detached-child cleanup ->
  `tests/test_workflow_job_dispatcher_cancellation.py`
- Reap-before-terminal ordering ->
  `tests/test_integrity_a_terminal_cas.py`
- Schema and migration guards -> `tests/test_schema_migrations.py`

## Exit Boundary

The slice proves local OS process containment on supported test platforms. It
does not claim container-orchestrator or remote-worker termination.
