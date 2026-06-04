# TDD Grill Findings

### Finding 1: Tests Must Prove Backpressure Before Spawn

status: resolved

The TDD plan now includes a saturated-cap test that asserts the dispatcher does
not claim a reserved row when capacity is full.

### Finding 2: Poison Path Must Be Non-Terminal but Non-Dispatchable

status: resolved

The plan pins `status=parked` plus `parked_reason`, and dispatcher selection
excludes parked rows so idempotent reattach still sees the bad job without
looping.

### Finding 3: Heartbeat Ownership Must Be Compare-and-Set

status: resolved

The plan requires wrong-owner heartbeats to fail, preventing unrelated workers
from extending a stale lease.
