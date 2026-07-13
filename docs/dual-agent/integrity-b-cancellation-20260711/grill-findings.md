# PRD Grill Findings

### Finding 1: Parent-Only Proof Would Miss the Actual Failure

status: resolved

Checking only the worker return code can pass while a Claude or Codex child is
still running. The acceptance contract now requires both a group-existence
probe and a direct descendant `getpgid` failure after cancellation.

### Finding 2: A Dead Leader Can Still Have a Live Process Group

status: resolved

`os.getpgid(worker_pid)` fails after the session leader exits even when a child
survives under the original PGID. Cleanup now falls back to probing and
signaling the worker PID as the known group ID.

### Finding 3: Stale Failure Ordering Must Be Observable

status: resolved

A post-reap assertion alone would not prove whether failure was persisted before
or after process cleanup. The stale-lease test observes group absence from the
state completion boundary itself.

### Finding 4: Group Signaling Must Not Endanger the Dispatcher

status: resolved

Persisted PIDs can be stale or corrupt. The implementation refuses to signal a
PGID equal to the dispatcher's current process group, while preserving the
worker `PID == PGID` session invariant.
