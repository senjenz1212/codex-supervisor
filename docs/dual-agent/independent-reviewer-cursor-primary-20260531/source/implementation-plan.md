# Independent Reviewer Cursor Primary Implementation Plan

## Scope

Implement P1-P7 from the PRD with a narrow edit set:

- `supervisor/config.py`
- `supervisor/cursor_agent.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `supervisor/agent_mailbox.py`
- `scripts/probe_cursor_sdk_live.py`
- tests covering the public boundaries
- `docs/adr/` entry for the independent reviewer boundary rename

## Files / Modules To Touch

- `supervisor/config.py`: change the supervisor default reviewer output mode
  from `litellm_structured` to `cursor_sdk` while preserving explicit
  structured configuration.
- `supervisor/cursor_agent.py`: add reviewer assurance metadata, per-attempt
  diagnostics, prompt-size capture, bounded Cursor SDK execution, and
  Cursor-to-structured fallback behavior.
- `mcp_tools/codex_supervisor_stdio.py`: expose
  `independent_reviewer` payloads while preserving `cursor_review` aliases,
  propagate runtime/assurance/fallback metadata into tool calls and
  transcripts, and keep reviewer-unavailable recovery unchanged.
- `mcp_tools/codex_supervisor_workflow_cli.py`: keep reviewer configuration
  fields round-tripping through the CLI fallback.
- `supervisor/agent_mailbox.py`: update review-packet wording and requirement
  ids where safe so the new boundary is not newly mislabeled as Cursor-only.
- `scripts/probe_cursor_sdk_live.py`: load Codex MCP env, force
  `reviewer_output_mode="cursor_sdk"`, and record honest Cursor-key skips.
- `tests/test_cursor_agent.py`: add deterministic unit coverage for default
  runtime, probe skip/env load, diagnostics, timeout, fallback success, and
  fallback rejection.
- `tests/test_dual_agent_workflow_driver.py`: add workflow coverage for default
  Cursor, independent reviewer aliasing, fallback degraded payloads, both-fail
  recovery, and valid revise/deny blocking.
- `docs/adr/20260531-independent-reviewer-boundary.md`: record the
  `cursor_review` to `independent_reviewer` migration and assurance model.
- `docs/dual-agent/independent-reviewer-cursor-primary-20260531/test-evidence.md`:
  collect focused/full test receipts after implementation.

## Steps

1. Flip the supervisor default reviewer output mode to `cursor_sdk`, while
   preserving explicit `litellm_structured` configuration and reviewer model
   threading.
2. Fix `scripts/probe_cursor_sdk_live.py` so it loads Codex MCP env, forces
   `cursor_sdk`, and skips honestly when no Cursor key exists.
3. Add prompt-size and per-attempt metadata to `CursorInvocationResult` details
   without changing the typed outcome contract.
4. Bound Cursor SDK execution with supervisor-side timeout handling, returning
   recoverable infrastructure failure on timeout.
5. Add a fallback path from Cursor SDK failure to `litellm_structured` when
   OpenAI-compatible credentials are configured. Record fallback reason,
   original failure, runtime, and lower assurance.
6. Add `independent_reviewer` payloads and transcript/export fields while
   preserving `cursor_review` compatibility aliases.
7. Update review-packet language in `agent_mailbox.py` to avoid new Cursor
   wording for non-Cursor runtimes.
8. Add deterministic tests and update existing tests whose old expectation was
   Gemini-as-default.
9. Run focused reviewer/workflow tests, `git diff --check`, and the full suite.

## Safety Rails

- Do not count missing reviewer verdicts as accept.
- Do not change Claude `/lead` or `lead_direct` defaults.
- Do not give Gemini tools or cwd.
- Do not weaken `evaluate_outcome_fidelity`.
- Do not bypass reviewer-unavailable recovery when both runtimes fail.

## Risks

- Cursor SDK wait/text calls may not expose a native timeout. Mitigation: bound
  the call in a supervisor-owned execution wrapper or deterministic timeout
  seam, and classify timeout as recoverable infrastructure.
- Fallback could accidentally run too early and mask Cursor contract bugs.
  Mitigation: fallback only after the Cursor retry/timeout envelope has
  produced a recoverable failure.
- The rename from `cursor_review` to `independent_reviewer` could break existing
  tests and readers. Mitigation: add the new field first, retain
  `cursor_review` as an alias, and add alias regression tests.
- Text-only fallback could be overtrusted. Mitigation: record assurance as
  degraded/lower-assurance and include original Cursor failure metadata.
- Test suite could become credential-dependent. Mitigation: all new tests mock
  SDK/OpenAI below the public boundary and keep live probes as artifacts only.

## Traceability

| PRD promise | Implementation step | TDD coverage |
|---|---|---|
| P1 | Steps 1, 6 | `test_select_reviewer_defaults_to_cursor_sdk_primary`, `test_workflow_invokes_cursor_sdk_by_default`, `test_workflow_kwargs_from_payload_preserves_reviewer_fields` |
| P2 | Step 2 | `test_probe_cursor_sdk_live_forces_cursor_runtime`, `test_probe_cursor_sdk_live_loads_codex_mcp_env` |
| P3 | Steps 3, 4 | `test_cursor_sdk_attempt_metadata_records_prompt_size_and_status`, `test_cursor_sdk_timeout_classifies_infrastructure_unavailable` |
| P4 | Step 5 | `test_cursor_sdk_failure_falls_back_to_litellm_structured`, `test_fallback_reviewer_revise_blocks_workflow` |
| P5 | Steps 6, 7 | `test_independent_reviewer_payload_aliases_cursor_review`, `test_no_cursor_label_for_litellm_runtime` |
| P6 | Steps 5, 7 | `test_both_reviewer_runtimes_fail_uses_reviewer_unavailable_recovery`, `test_real_cursor_or_fallback_deny_still_blocks` |
| P7 | Steps 8, 9 | focused reviewer/workflow pytest, `git diff --check`, full suite |
