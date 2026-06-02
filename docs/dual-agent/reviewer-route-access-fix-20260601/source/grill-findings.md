# PRD Grill Findings: Reviewer Route Access Fix

### Finding 1: Access denial must not be grouped with recoverable infra

Status: resolved

Evidence: The Phase 0 probe showed the fallback route can authenticate when the
supervisor secret-loading path is used, while the product prompt still calls out
403/Tessen as a possible route failure. Grouping 401/403 with transient
reviewer infrastructure would allow policy recovery to hide a fixable
configuration problem.

Resolution: P2 and P3 require `reviewer_access_denied`, `recoverable=false`, no
retry, and no reviewer-unavailable degraded recovery. The implementation plan
touches both `supervisor/cursor_agent.py` and the workflow objection/recovery
classification in `mcp_tools/codex_supervisor_stdio.py`.

### Finding 2: Fallback success needs prompt bounding, not only more tokens

Status: resolved

Evidence: Phase 0 large fallback packet authenticated but returned
`finish_reason=length`; the bounded packet returned a typed accept. This means
the route is viable but prompt size can destroy the typed verdict.

Resolution: P1 requires compact structured fallback prompts. The compact packet
keeps artifact, receipt, Claude outcome, and critical-review signals while
truncating bulky receipt fields and transcript-shaped values.

### Finding 3: Cursor investigation must be separated from fallback hardening

Status: resolved

Evidence: Cursor SDK failed on large, bounded, and tiny packets with the same
`internal: internal error`. That does not support a prompt-size-only fix in this
slice.

Resolution: P4 preserves Cursor as primary and keeps the bounded retry behavior.
The Phase 0 artifact records the Cursor follow-up evidence; this slice does not
claim Cursor is fixed.

### Finding 4: Surfacing must be deterministic and replayable

Status: resolved

Evidence: The supervisor already exports `tri_agent_cursor_review`,
interactions, and transcript projections. Adding a new recovery path for access
denied would risk confusing access/config failure with reviewer-unavailable
recovery.

Resolution: P3 requires a distinct classification and objection while reusing
existing replay events. Tests assert no degraded recovery event is written for
access-denied failures.
