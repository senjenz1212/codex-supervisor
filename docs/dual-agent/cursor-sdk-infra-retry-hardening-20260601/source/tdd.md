# Cursor SDK Infrastructure Retry Hardening TDD Plan

## RED Plan

### test_cursor_sdk_infra_retry_succeeds_before_fallback

Issue: Issue 1

PRD promise: P1

Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`

Maps to: Issue 1 / P1

RED: With `reviewer_infra_retry_limit=1`, fake `_run_cursor_sdk` raises
`RuntimeError("internal: internal error")` on the first attempt and returns a
valid typed outcome on the second. The current code returns
`reviewer_infrastructure_unavailable` immediately, so the test fails before the
fix.

GREEN: The result is `cursor_review_ok`, two SDK calls were made,
fallback was not called, and diagnostics report one infrastructure retry.

### test_cursor_sdk_infra_retries_exhaust_with_attempt_diagnostics

Issue: Issue 1

PRD promise: P2

Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`

Maps to: Issue 1 / P2

RED: With `reviewer_infra_retry_limit=2`, fake `_run_cursor_sdk`
raises on all attempts. The current code records only the first failure.

GREEN: The result is recoverable
`reviewer_infrastructure_unavailable`, three attempts are recorded, and
diagnostics include every attempt reason/error plus the configured retry limit.

### test_cursor_sdk_contract_retry_does_not_consume_infra_retry_budget

Issue: Issue 1

PRD promise: P3

Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`

Maps to: Issue 1 / P3

RED: Fake `_run_cursor_sdk` returns malformed review text and set both
contract and infra retry limits. The test asserts the corrective contract packet
is used and fallback is not called.

GREEN: Contract retry behavior remains unchanged; infra retry count
is zero because the SDK call itself succeeded.

### test_cursor_sdk_missing_module_does_not_consume_infra_retry_budget

Issue: Issue 1

PRD promise: P1, P3

Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`

Maps to: Issue 1 / P1 / P3

RED: Fake `_run_cursor_sdk` raises `ModuleNotFoundError(name="cursor_sdk")`
with `reviewer_infra_retry_limit=5`. A retry loop would make multiple SDK
calls or add infrastructure retry diagnostics, violating the PRD's forbidden
outcome for missing local modules.

GREEN: The result is recoverable
`reviewer_infrastructure_unavailable` with original reason
`cursor_sdk_missing`, exactly one SDK call, no infrastructure retry diagnostics,
and no missing verdict counted as an accept.

### test_workflow_kwargs_from_payload_preserves_reviewer_infra_retry_fields

Issue: Issue 2

PRD promise: P4

Public boundary: `mcp_tools.codex_supervisor_workflow_cli.workflow_kwargs_from_payload`

Maps to: Issue 2 / P4

RED: A workflow JSON payload containing `reviewer_infra_retry_limit`
and `reviewer_infra_retry_backoff_s` loses those fields today.

GREEN: Both fields are preserved in kwargs while unknown fields are
still dropped.

### test_submit_workflow_job_payload_round_trips_reviewer_infra_retry_policy

Issue: Issue 2

PRD promise: P4

Public boundary: `mcp_tools.codex_supervisor_stdio.submit_dual_agent_workflow_job`

Maps to: Issue 2 / P4

RED: A detached submit with `reviewer_infra_retry_limit` and
`reviewer_infra_retry_backoff_s` writes a job request payload that drops those
fields, so a reconnecting or CLI worker cannot preserve the configured policy.

GREEN: The detached request JSON stores both fields, and
`workflow_kwargs_from_payload` round-trips both values into the workflow call.

### test_workflow_passes_reviewer_infra_retry_policy_to_cursor_request

Issue: Issue 2

PRD promise: P4

Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`

Maps to: Issue 2 / P4

RED: A workflow call with retry fields invokes the fake Cursor runner
without those values on `CursorInvocationRequest`.

GREEN: Every Cursor request receives the configured retry limit and
backoff value, and workflow route metadata records the same values.

### test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection

Issue: Issue 3

PRD promise: P5

Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`

Maps to: Issue 3 / P5

RED: Existing behavior should already pass. Keep this as a regression
guard with retry policy enabled so retry hardening cannot turn valid Cursor
`revise` into degraded progress.

GREEN: The workflow blocks on the valid Cursor `revise` decision.

### test_exhausted_cursor_infra_retry_proceeds_degraded_without_counting_cursor_accept

Issue: Issue 3

PRD promise: P5

Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`

Maps to: Issue 3 / P5

RED: Fake the Cursor runner returning recoverable
`reviewer_infrastructure_unavailable` with exhausted infra-retry diagnostics
under `reviewer_unavailable_policy=proceed_degraded`. Without the recovery
boundary, the workflow blocks as reviewer unavailable or incorrectly treats the
missing Cursor verdict as accept.

GREEN: The workflow advances on Claude+Codex only, records degraded reviewer
unavailable recovery evidence, preserves exhausted retry diagnostics, and
`reviewer_verdict_counted_as_accept` remains false.

## GREEN Plan

1. Extend `CursorInvocationRequest` with retry limit/backoff fields.
2. Refactor Cursor SDK invocation into an infrastructure-attempt loop that
   catches exceptions/timeouts, records diagnostics, sleeps between retryable
   attempts, and falls through to existing fallback after exhaustion.
3. Keep contract retry logic after a successful SDK call exactly as it is today.
4. Add supervisor config defaults and thread values through workflow API,
   detached submit payloads, CLI payload filtering, and route metadata.
5. Run the focused Cursor and workflow tests, then the full suite.

## Regression Commands

- `uv run --extra dev pytest tests/test_cursor_agent.py -q`
- `uv run --extra dev pytest tests/test_dual_agent_workflow_driver.py::test_workflow_kwargs_from_payload_preserves_reviewer_infra_retry_fields tests/test_dual_agent_workflow_driver.py::test_submit_workflow_job_payload_round_trips_reviewer_infra_retry_policy tests/test_dual_agent_workflow_driver.py::test_workflow_passes_reviewer_infra_retry_policy_to_cursor_request tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection tests/test_dual_agent_workflow_driver.py::test_exhausted_cursor_infra_retry_proceeds_degraded_without_counting_cursor_accept -q`
- `uv run --extra dev pytest -q`

## Traceability

- P1 maps to `test_cursor_sdk_infra_retry_succeeds_before_fallback`.
- P2 maps to
  `test_cursor_sdk_infra_retries_exhaust_with_attempt_diagnostics`.
- P3 maps to
  `test_cursor_sdk_contract_retry_does_not_consume_infra_retry_budget`
  and `test_cursor_sdk_missing_module_does_not_consume_infra_retry_budget`.
- P4 maps to
  `test_workflow_kwargs_from_payload_preserves_reviewer_infra_retry_fields`
  and `test_submit_workflow_job_payload_round_trips_reviewer_infra_retry_policy`
  and `test_workflow_passes_reviewer_infra_retry_policy_to_cursor_request`.
- P5 maps to `test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection`
  and
  `test_exhausted_cursor_infra_retry_proceeds_degraded_without_counting_cursor_accept`.
