# Reviewer Contract Reliability TDD Plan

## Public Boundaries

- `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
- `mcp_tools.codex_supervisor_workflow_cli.workflow_kwargs_from_payload`
- `supervisor.cursor_agent.invoke_cursor_agent`

The first RED test for the implementation hits the reviewer invocation
boundary, because the live failure is an independent reviewer result that does
not produce a valid typed outcome.

## Test Cases

### test_select_reviewer_defaults_to_phase0_structured_gemini

Maps to: ISS-1, P1, P3
RED: Current defaults still select Cursor SDK `composer-2.5`.
GREEN: Add reviewer config defaults for `gemini-3.1-pro-preview` and structured
LiteLLM output mode while preserving explicit `cursor_model` compatibility.

### test_workflow_kwargs_from_payload_preserves_reviewer_fields

Maps to: ISS-1, P1
RED: CLI workflow payload drops `reviewer_model` and
`reviewer_output_mode`.
GREEN: Add the fields to workflow keys and MCP parameter threading.

### test_structured_litellm_reviewer_returns_fidelity_passing_outcome

Maps to: ISS-2, P2, P6
RED: `invoke_cursor_agent` cannot use a structured OpenAI-compatible reviewer
client.
GREEN: Add a structured reviewer path that emits JSON schema output, wraps it
for `evaluate_outcome_fidelity`, and returns a green `CursorInvocationResult`.

### test_cursor_sdk_output_mode_routes_to_cursor_sdk_runner

Maps to: ISS-1, ISS-2, P1
RED: Changing the default reviewer route to structured LiteLLM can make the
legacy Cursor SDK path unreachable.
GREEN: With `reviewer_output_mode=cursor_sdk`, `invoke_cursor_agent` still
dispatches through the existing Cursor SDK runner and preserves explicit
`cursor_model` compatibility.

### test_structured_litellm_reviewer_preserves_read_only_guard

Maps to: ISS-2, P2
RED: A structured reviewer implementation can bypass the before/after git
status guard and silently allow reviewer-side worktree edits.
GREEN: The same read-only guard wraps the structured path; if the reviewer path
changes git status, the result is blocked as `cursor_modified_worktree`.

### test_structured_litellm_reviewer_enforces_contract_completeness

Maps to: ISS-2, P2
RED: A schema-valid payload missing the critical-review completeness contract
is accepted.
GREEN: Reuse `_evaluate_cursor_contract_completeness` for structured output.

### test_structured_litellm_reviewer_revise_blocks_workflow

Maps to: ISS-3, P4
RED: A valid structured reviewer `revise` is treated like unavailable
infrastructure or proceed-degraded.
GREEN: Preserve the existing AND-verdict path for valid non-accept outcomes.

### test_structured_litellm_reviewer_deny_blocks_workflow

Maps to: ISS-3, P4
RED: A valid structured reviewer `deny` is treated like unavailable
infrastructure or proceed-degraded.
GREEN: Preserve the same blocking behavior for `deny` as for `revise`.

### test_structured_litellm_failure_classifies_as_infrastructure_unavailable

Maps to: ISS-3, P5
RED: Gateway/model exceptions escape or become fake accepts.
GREEN: Return a recoverable `reviewer_infrastructure_unavailable` result.

### test_structured_litellm_invalid_json_classifies_as_contract_unmet

Maps to: ISS-3, P5
RED: Invalid structured output is swallowed or accepted.
GREEN: Classify invalid/missing structured output as recoverable
`reviewer_contract_unmet`.

### test_structured_litellm_truncation_classifies_as_contract_unmet

Maps to: ISS-3, P5
RED: A gateway response with `finish_reason=length` and partial JSON is
silently accepted or misclassified.
GREEN: Truncated structured output is never accepted and is classified as
recoverable `reviewer_contract_unmet`.

### test_reviewer_route_metadata_is_recorded

Maps to: ISS-2, ISS-4, P3, P6
RED: Cursor review payload records only model and omits route/output mode.
GREEN: Include reviewer runtime, model, and output mode in reviewer result
metadata and tool-call summary.

### test_workflow_invokes_reviewer_after_claude_accept_with_non_claude_default

Maps to: ISS-2, P3
RED: A workflow can invoke the independent reviewer before Claude synthesis or
default to a Claude-lineage reviewer.
GREEN: The workflow invokes the reviewer only after an accepted Claude gate
payload exists, and default reviewer metadata records the Gemini LiteLLM route.

## RED/GREEN Plan

1. RED: Add config/CLI round-trip tests for reviewer fields.
   GREEN: Add config model, MCP parameters, job payload fields, and CLI keys.

2. RED: Add structured reviewer unit test with a fake OpenAI-compatible client.
   GREEN: Implement the structured LiteLLM path behind the existing
   `CursorInvocationRequest`/`CursorInvocationResult` boundary.

3. RED: Add compatibility and guard tests.
   GREEN: Keep the Cursor SDK path reachable via explicit output mode and keep
   the git-status read-only guard around every reviewer route.

4. RED: Add failure, invalid-output, and truncation classifications.
   GREEN: Map gateway errors to infrastructure unavailable and parser/schema
   failures to contract unmet without changing recovery policy.

5. RED: Add workflow regressions for valid `revise` and `deny`.
   GREEN: Ensure valid reviewer outcomes still use existing blocking algebra.

6. RED: Add workflow ordering/lineage metadata test.
   GREEN: Preserve downstream reviewer execution after Claude acceptance and
   record Gemini LiteLLM as the non-Claude default reviewer route.

7. Refactor: Keep schema construction and validation small, deterministic, and
   shared by tests and runtime. Do not broaden scope into reviewer registries.
