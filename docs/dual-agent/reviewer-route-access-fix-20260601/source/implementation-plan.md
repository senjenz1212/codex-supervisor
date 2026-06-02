# Implementation Plan: Reviewer Route Access Fix

## Files / Modules To Touch

- `supervisor/cursor_agent.py`: add access-denied classification, sanitized
  diagnostics, no-retry handling, and compact structured reviewer prompt
  generation.
- `mcp_tools/codex_supervisor_stdio.py`: keep access-denied outside
  reviewer-unavailable recovery and surface a distinct gate objection.
- `tests/test_cursor_agent.py`: cover direct structured 403, Cursor SDK
  permission failure, prompt compaction, and existing fallback behavior.
- `tests/test_dual_agent_workflow_driver.py`: cover workflow blocking without
  degraded recovery for `reviewer_access_denied`.
- `docs/dual-agent/reviewer-route-access-fix-20260601/*`: preserve PRD/TDD,
  Phase 0 evidence, workflow request/result, transcripts, and receipts.

## Steps

1. Add RED tests for access-denied classification and workflow non-recovery.
2. Implement classifier helpers and access-denied result construction in
   `supervisor/cursor_agent.py`.
3. Add compact structured reviewer prompt helpers and use them for
   `litellm_structured` calls.
4. Update workflow objection/recovery classification so access-denied remains a
   normal blocking review failure, not recoverable infrastructure.
5. Run focused tests, then the full suite, then run the supervised workflow with
   the Phase 0 artifact attached.

## Risks

- Over-broad message matching could classify ordinary validation failures as
  access denied. The classifier therefore prefers explicit HTTP status codes and
  narrowly scoped access/permission markers.
- Over-compaction could remove evidence needed for rigorous review. The compact
  prompt keeps artifact paths, receipt ids, statuses, claims, changed files,
  tests, Claude summary, confidence, and critical-review fields.
- Cursor SDK remains externally failing. This plan does not claim Cursor is
  fixed; it preserves primary-failure diagnostics and relies on the proven
  fallback route for counted review.
- Workflow recovery code is safety-sensitive. The implementation changes only
  the recoverable-classification boundary and objection text; real revise/deny
  and genuine unavailable recovery tests remain in place.

## Traceability

- P1 -> `test_structured_fallback_prompt_compacts_large_receipts`,
  `test_cursor_sdk_failure_falls_back_to_litellm_structured_with_lower_assurance`
- P2 -> `test_structured_litellm_access_denied_classifies_distinctly_without_retry`,
  `test_cursor_sdk_access_denied_does_not_retry_or_fallback`
- P3 -> `test_reviewer_access_denied_blocks_without_degraded_recovery`
- P4 -> `test_cursor_sdk_failure_falls_back_to_litellm_structured_with_lower_assurance`
- P5 -> `test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection`

## Verification

- `uv run --extra dev pytest tests/test_cursor_agent.py tests/test_dual_agent_workflow_driver.py -q`
- `uv run --extra dev pytest -q`
- `codex-supervisor-workflow` with `task_id=reviewer-route-access-fix-20260601`,
  `cursor_review=true`, `cursor_review_profile=rigorous`, and the Phase 0
  reviewer probe attached as immutable planning evidence.
