# PRD Grill Findings

### Finding 1

status: resolved

Concern: Retrying every Cursor failure could weaken the independent reviewer by
papering over real `revise` or `deny` decisions.

Resolution: The PRD limits infrastructure retries to exceptions and watchdog
timeouts before a typed Cursor verdict exists. Valid Cursor outcomes keep the
existing AND-gate semantics and still block when they return `revise` or `deny`.

### Finding 2

status: resolved

Concern: A retry policy without diagnostics would make the next outage harder
to audit.

Resolution: P2 requires attempt history and final error diagnostics in the
reviewer result. The implementation plan maps this to
`tests/test_cursor_agent.py::test_cursor_sdk_infra_retries_exhaust_with_attempt_diagnostics`.

### Finding 3

status: resolved

Concern: Fallback could run too early and mask Cursor SDK instability.

Resolution: P1 and P2 require fallback only after the configured SDK
infrastructure retry budget is exhausted. Tests assert fallback is not invoked
when the second SDK attempt succeeds.

### Finding 4

status: resolved

Concern: Inline workflow and detached workflow submit might diverge.

Resolution: P4 requires the same retry fields to be accepted by
`run_dual_agent_workflow`, `submit_dual_agent_workflow_job`, and the CLI payload
filter.
