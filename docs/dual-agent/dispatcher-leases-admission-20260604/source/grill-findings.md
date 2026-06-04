# PRD Grill Findings

### Finding 1: Poll Compatibility Must Not Become a Second Spawn Path

status: resolved

The PRD originally described a long-lived dispatcher but did not state how the
existing durable submit/poll tool pair remains usable during the transition.
Resolution: any compatibility redrive from `poll_dual_agent_workflow_job` must
call the same dispatcher implementation, preserving one spawn-transition code
path.

### Finding 2: Lease Expiry on Spawned Workers Must Not Duplicate Work

status: resolved

A spawned job with an expired lease cannot safely be respawned because the old
worker may still have made external progress. Resolution: the reaper may
complete from an existing result file or fail the job, but must not respawn a
`spawned` row.

### Finding 3: Backoff Needs a Single Owner

status: resolved

Retry throttling must live in the dispatcher, not in submit, poll, and worker
code independently. Resolution: the dispatcher owns retry counters,
`next_dispatch_at`, and poison parking.
