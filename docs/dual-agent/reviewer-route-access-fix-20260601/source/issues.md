# Issues: Reviewer Route Access Fix

## Slice 1: Classify reviewer access denied

Priority: P0

Scope: Add `reviewer_access_denied` to cursor invocation results and detect
HTTP 401/403 or equivalent gateway permission failures without logging secrets.

PRD promise: P2, P3

Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`

Acceptance criteria:

- [ ] A structured reviewer 403 produces `failure_classification=reviewer_access_denied`.
- [ ] The access-denied result has `recoverable=false` and one attempt.
- [ ] Diagnostics include sanitized status/model/base-url host context and no secret value.
- [ ] Access-denied failures are not retried as contract or infrastructure failures.

## Slice 2: Bound structured fallback review packets

Priority: P0

Scope: Compact the prompt used by `litellm_structured` so large receipts and
Claude outcome payloads do not consume the whole completion budget.

PRD promise: P1, P4

Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`

Acceptance criteria:

- [ ] Cursor SDK infrastructure failure falls back to `litellm_structured` when an explicit key is configured.
- [ ] The fallback prompt contains the typed outcome contract and critical review instructions.
- [ ] Large receipt `result` or transcript-like fields are truncated in the prompt.
- [ ] Runtime and fallback diagnostics truthfully identify Cursor primary and Gemini fallback.

## Slice 3: Keep access denied outside degraded workflow recovery

Priority: P0

Scope: Ensure the workflow does not treat `reviewer_access_denied` as
recoverable reviewer-unavailable infrastructure.

PRD promise: P2, P3, P5

Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`

Acceptance criteria:

- [ ] A workflow with reviewer access denied blocks instead of proceeding degraded.
- [ ] `tri_agent_cursor_review` records the distinct classification.
- [ ] No `dual_agent_reviewer_unavailable_recovery` proceed-degraded event is written.
- [ ] The gate-round objection names reviewer access denied.

## Slice 4: Preserve reviewer algebra regressions

Priority: P1

Scope: Keep existing reviewer behavior for successful fallback, genuine
transient both-down recovery, and real reviewer revise/deny decisions.

PRD promise: P1, P4, P5

Public boundary: focused reviewer and workflow tests.

Acceptance criteria:

- [ ] A fallback typed accept is counted as reviewer accept.
- [ ] A valid reviewer revise or deny still blocks.
- [ ] Genuine infrastructure unavailable remains eligible for existing degraded recovery policy.
- [ ] Focused and full test suites remain green.
