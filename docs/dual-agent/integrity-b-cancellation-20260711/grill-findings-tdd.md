# TDD Grill Findings

### Finding 1: Mocked Signals Would Not Prove Descendant Termination

status: resolved

The tests launch real POSIX subprocesses with `start_new_session=True`, capture
the parent, child, and PGID from a readiness artifact, and inspect the operating
system after cleanup. No mocked `killpg` call can satisfy the descendant proof.

### Finding 2: The Test Must Force the KILL Branch

status: resolved

A normal worker could exit on `SIGTERM`, leaving escalation untested. The test
process tree contains a `SIGTERM`-resistant member, so cleanup must cross the
bounded wait and remove the group with `SIGKILL`.

### Finding 3: Stale-Lease Postconditions Alone Do Not Prove Ordering

status: resolved

The stale test wraps the state completion boundary and records whether the PGID
is already absent. This distinguishes kill-before-fail from fail-then-kill.

### Finding 4: Readiness and Cleanup Must Prevent Test Orphans

status: resolved

The child writes a readiness file only after its signal behavior and PGID are
established. Every test uses a `finally` cleanup that sends group `SIGKILL` and
waits for the direct worker, including RED failures.

### Finding 5: Existing Fake-PID Reaper Coverage Must Survive

status: resolved

Focused verification includes the existing dead spawned-worker test. A missing
persisted process group remains a successful no-op before the stale job is
marked failed.
