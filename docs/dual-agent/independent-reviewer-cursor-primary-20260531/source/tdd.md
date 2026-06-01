# Independent Reviewer Cursor Primary TDD Plan

## Public Boundaries

- `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
- `mcp_tools.codex_supervisor_workflow_cli.workflow_kwargs_from_payload`
- `supervisor.cursor_agent.invoke_cursor_agent`
- `scripts/probe_cursor_sdk_live.py`
- exported workflow/transcript payloads under `docs/dual-agent/<task_id>/`

The first RED tests hit public workflow and reviewer invocation boundaries.
Model and SDK I/O are mocked below those boundaries except for the live phase0
probe artifacts.

## Test Cases

### test_select_reviewer_defaults_to_cursor_sdk_primary

Maps to: ISS-1, P1
RED: default reviewer output mode is `litellm_structured`.
GREEN: default reviewer output mode is `cursor_sdk`, while explicit structured
mode still selects the configured Gemini reviewer.

### test_workflow_invokes_cursor_sdk_by_default

Maps to: ISS-1, P1, P5
RED: workflow default reviewer request uses `litellm_structured`.
GREEN: workflow default reviewer request uses `cursor_sdk` and records
`independent_reviewer.reviewer_runtime="cursor_sdk"`.

### test_workflow_kwargs_from_payload_preserves_reviewer_fields

Maps to: ISS-1, P1
RED: CLI fallback payload filtering drops explicit reviewer fields.
GREEN: `workflow_kwargs_from_payload` preserves `reviewer_model`,
`reviewer_output_mode`, and `reviewer_max_tokens` so operators can force either
runtime without code edits.

### test_probe_cursor_sdk_live_forces_cursor_runtime

Maps to: ISS-1, P2
RED: probe inherits the global default and can fail on missing
`OPENAI_API_KEY`.
GREEN: probe sets `reviewer_output_mode="cursor_sdk"` and missing key produces
`missing_cursor_api_key`.

### test_probe_cursor_sdk_live_loads_codex_mcp_env

Maps to: ISS-1, P2
RED: probe ignores `~/.codex/config.toml` MCP env and skips even when the
supervisor workflow would have a Cursor key.
GREEN: probe loads Codex MCP env exactly like the workflow CLI.

### test_cursor_sdk_attempt_metadata_records_prompt_size_and_status

Maps to: ISS-2, P3
RED: empty transcript failure has no prompt chars or raw status detail.
GREEN: result details include prompt chars, attempt metadata, run id, agent id,
status, duration, model, and retry reason.

### test_cursor_sdk_timeout_classifies_infrastructure_unavailable

Maps to: ISS-2, P3, P6
RED: blocking SDK text/wait call can hang past `timeout_s`.
GREEN: timeout returns recoverable `reviewer_infrastructure_unavailable` with
timeout diagnostics.

### test_cursor_sdk_failure_falls_back_to_litellm_structured

Maps to: ISS-3, P4
RED: Cursor SDK failure dead-ends before trying configured fallback.
GREEN: fallback returns a valid outcome with runtime
`litellm_structured`, lower assurance, and original failure details.

### test_fallback_reviewer_revise_blocks_workflow

Maps to: ISS-3, P4, P6
RED: fallback revise can be treated as degraded accept.
GREEN: valid fallback revise blocks under existing AND algebra.

### test_both_reviewer_runtimes_fail_uses_reviewer_unavailable_recovery

Maps to: ISS-3, P6
RED: both-fail either crashes or fakes a verdict.
GREEN: both-fail returns recoverable reviewer unavailable classification and
the workflow uses `reviewer_unavailable_policy`.

### test_independent_reviewer_payload_aliases_cursor_review

Maps to: ISS-4, P5
RED: payload only has `cursor_review`.
GREEN: payload has `independent_reviewer` and a compatibility `cursor_review`
alias pointing to the same reviewer result.

### test_no_cursor_label_for_litellm_runtime

Maps to: ISS-4, P5
RED: Gemini fallback is recorded as Cursor accepted.
GREEN: exported payloads and review packets record independent reviewer wording
and `reviewer_runtime="litellm_structured"`.

### test_real_cursor_or_fallback_deny_still_blocks

Maps to: ISS-3, ISS-4, P6
RED: valid deny can be classified as unavailable infrastructure.
GREEN: valid deny blocks and is not recoverable.

## RED/GREEN Sequence

1. RED default/probe tests, then flip config default and fix probe runtime/env.
2. RED diagnostic/timeout tests, then add attempt metadata and bounded SDK
   invocation.
3. RED fallback tests, then add fallback execution and lower-assurance fields.
4. RED alias/export tests, then add `independent_reviewer` while preserving
   `cursor_review`.
5. RED rejection/both-fail workflow tests, then preserve existing safety
   algebra and recovery.
6. Refactor names and docs, then run focused and full suite.
