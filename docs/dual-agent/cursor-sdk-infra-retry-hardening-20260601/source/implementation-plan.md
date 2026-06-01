# Implementation Plan: Cursor SDK Infrastructure Retry Hardening

## Files / Modules To Touch

- `supervisor/cursor_agent.py`: add Cursor SDK infrastructure retry/backoff,
  retry diagnostics, and request fields.
- `supervisor/config.py`: add supervisor retry defaults.
- `mcp_tools/codex_supervisor_stdio.py`: thread retry policy through inline and
  detached workflow calls and route metadata.
- `mcp_tools/codex_supervisor_workflow_cli.py`: preserve retry fields from JSON
  workflow payloads.
- `tests/test_cursor_agent.py`: add public-boundary retry and diagnostics tests.
- `tests/test_dual_agent_workflow_driver.py`: add workflow payload/threading and
  gate-safety regression tests.

## Planned Changes

1. Add `reviewer_infra_retry_limit` and `reviewer_infra_retry_backoff_s` to
   `CursorInvocationRequest`.
2. Introduce an internal Cursor SDK infrastructure attempt loop. Retry
   `CursorSdkTimeoutError` and generic SDK exceptions, but do not retry missing
   modules.
3. Accumulate compact attempt diagnostics and merge them into successful or
   exhausted results.
4. Invoke existing fallback only after infrastructure retry exhaustion.
5. Add config defaults and helper normalization for retry values.
6. Thread the values through `run_dual_agent_workflow`,
   `submit_dual_agent_workflow_job`, MCP wrappers, CLI payload filtering, and
   workflow route metadata.
7. Keep existing contract retry and valid Cursor decision semantics unchanged.

## Guardrails

- Missing Cursor verdicts remain non-accepting evidence.
- Cursor `revise` or `deny` continues to block the gate.
- Fallback does not run before SDK infrastructure retries are exhausted.
- Contract retries and infrastructure retries remain separate budgets.
- No change to the default Cursor model or reviewer-unavailable policy.

## Risks

- Retry can lengthen gates when Cursor is truly down. Mitigate with bounded
  defaults and configurable backoff.
- Retrying watchdog timeouts can duplicate slow work. Mitigate by keeping the
  limit small and preserving timeout diagnostics.
- Extra route fields can affect idempotent job token derivation. This is
  acceptable because different retry policy is a different logical request.

## Acceptance Map

- P1: transient SDK exception succeeds on retry before fallback.
- P2: exhausted retries produce typed unavailable evidence with attempt history.
- P3: malformed output uses contract retry only, and missing local modules do
  not consume infrastructure retry budget.
- P4: workflow and CLI payloads carry retry policy.
- P5: real Cursor `revise` still blocks, and exhausted infra retry recovery
  advances only as degraded, non-accepting Cursor evidence.

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
