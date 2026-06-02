# PRD: Reviewer Route Access Fix

## Problem Statement

Rigorous dual-agent runs need at least one independent reviewer verdict that is
real, typed, and counted. The current reviewer stack is honest about failure,
but it can rest in degraded mode: Cursor SDK reports `internal: internal error`,
while earlier Gemini/LiteLLM fallback attempts were treated as transient
reviewer infrastructure even when the failure was an access or configuration
problem. A missing or malformed reviewer verdict must never count as accept, yet
an actually working fallback reviewer should carry the independent-review gate
when Cursor is externally unavailable.

Phase 0 runtime evidence resolved the route question for this slice. With the
supervisor secret-loading path, the LiteLLM/Gemini route authenticates to
`gemini-3.1-pro-preview`; a large reviewer packet finished with `length`, while
a bounded representative packet returned a valid typed accept. Cursor SDK failed
with `internal: internal error` on large, bounded, and tiny packets, so this
slice treats Cursor as still-primary but externally unavailable until a separate
Cursor route investigation proves otherwise.

## Solution

Keep Cursor SDK as the primary reviewer and LiteLLM/Gemini as the fallback, but
make the fallback reliable enough for rigorous review and make access failures
impossible to hide inside degraded recovery. Structured fallback prompts will be
bounded and evidence-preserving: they keep artifact paths, receipt ids/statuses,
Claude outcome summary, changed files, tests, confidence, and critical-review
signals, while truncating large command output or transcript-shaped payloads.

Add a `reviewer_access_denied` classification for HTTP 401/403 or equivalent
gateway permission errors. This classification is not retried, is not treated as
recoverable reviewer-unavailable infrastructure, and blocks with a distinct
configuration/access reason. Genuine transient both-down failures keep the
existing reviewer-unavailable recovery net. Real reviewer revise or deny verdicts
continue to block.

## User Stories

- As an operator, I want rigorous runs to get a real counted independent-review
  verdict from Cursor or Gemini instead of settling into degraded mode.
- As a supervisor maintainer, I want 401/403 access problems to surface as
  configuration failures that are fixed at the route or token layer.
- As an auditor, I want reviewer runtime, fallback reason, typed verdict, and
  failure classification recorded truthfully in ledger events and transcripts.
- As a quality owner, I want missing reviewer verdicts, access failures, and
  real revise or deny decisions to stay non-accepting.
- As a future Cursor investigator, I want the current Cursor failure evidence
  preserved without blocking this fallback-hardening slice.

## PRD Promise Contracts

P1. fallback-gemini-produces-counted-typed-verdict

- User-visible promise: When Cursor SDK is unavailable and the configured
  Gemini/LiteLLM route is authorized, rigorous review can advance on a real
  typed fallback verdict rather than degraded recovery.
- Representative prompts or actions: Run a rigorous workflow gate with Cursor
  SDK failing as infrastructure and an explicit structured fallback key.
- Public boundary: `supervisor.cursor_agent.invoke_cursor_agent` as called by
  `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
- Allowed outcomes: fallback result has `reviewer_runtime=litellm_structured`,
  `fallback_from_runtime=cursor_sdk`, a valid `<dual_agent_outcome>`, and
  `accepted=true` only when the typed verdict accepts.
- Forbidden outcomes: proceed-degraded with no reviewer verdict, fake accept,
  missing typed outcome, or untruthful runtime metadata.

P2. access-denied-is-distinct-and-non-retryable

- User-visible promise: HTTP 401/403 or permission-denied reviewer failures are
  classified as `reviewer_access_denied`, not transient infrastructure.
- Representative prompts or actions: Simulate a LiteLLM gateway 403 from the
  structured reviewer route.
- Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
- Allowed outcomes: one attempt, `failure_classification=reviewer_access_denied`,
  `recoverable=false`, red probe reason `reviewer_access_denied`, sanitized
  diagnostics, and no fallback/degraded recovery.
- Forbidden outcomes: retrying the same 403, classifying it as
  `reviewer_infrastructure_unavailable`, hiding it under reviewer-unavailable
  recovery, or logging a secret.

P3. access-denied-blocks-workflow-loudly

- User-visible promise: A reviewer access failure blocks as configuration/access
  failure and is visible in the gate result and transcript.
- Representative prompts or actions: Run `run_dual_agent_workflow` with Cursor
  review enabled and a reviewer runner returning `reviewer_access_denied`.
- Public boundary: `run_dual_agent_workflow`
- Allowed outcomes: final gate blocks; `tri_agent_cursor_review` records
  `reviewer_access_denied`; no `dual_agent_reviewer_unavailable_recovery`
  proceed-degraded event is written.
- Forbidden outcomes: missing verdict counted as accept, degraded auto-proceed,
  or vague `cursor_review_failed` with no access classification.

P4. cursor-primary-hierarchy-remains-truthful

- User-visible promise: Cursor remains primary, Gemini remains fallback, and the
  supervisor records which runtime actually produced the verdict.
- Representative prompts or actions: Cursor SDK fails, fallback succeeds; then
  inspect the result payload and `tri_agent_cursor_review` event.
- Public boundary: cursor invocation payload and workflow ledger events.
- Allowed outcomes: primary failure diagnostics are nested under fallback
  diagnostics, fallback assurance is lower than tool-backed primary, and
  `reviewer_runtime` names the actual responder.
- Forbidden outcomes: presenting Gemini as Cursor, dropping the primary failure
  context, or treating fallback text-only review as tool-backed Cursor.

P5. real-reviewer-rejection-still-blocks

- User-visible promise: A valid fallback or Cursor `revise`/`deny` verdict still
  blocks under the existing AND algebra.
- Representative prompts or actions: Return a typed reviewer outcome with
  decision `revise`.
- Public boundary: `run_dual_agent_workflow`
- Allowed outcomes: cursor review is present and non-accepting, the gate does
  not advance, and no infrastructure recovery path fires.
- Forbidden outcomes: treating a real rejection as unavailable infrastructure or
  proceeding degraded over a valid independent-review objection.

## Implementation Decisions

- Extend `CursorFailureClassification` with `reviewer_access_denied`.
- Detect access denial from exception status codes, response metadata, class
  names, and sanitized message markers such as 401, 403, permission denied, and
  access denied.
- Return a supervisor-authored access-denied result immediately; do not enter
  contract retries, Cursor infrastructure retries, or fallback recovery for that
  error.
- Compact structured reviewer prompts, especially receipt `result` fields and
  Claude outcome payloads, before sending to LiteLLM/Gemini.
- Keep Cursor SDK bounded retries from `01c0e66` and preserve the Phase 0 Cursor
  evidence as a follow-up note rather than changing the Cursor route here.
- Use existing `tri_agent_cursor_review` and gate-result evidence for surfacing;
  add distinct objection text for access denial instead of adding a new
  recovery class.

## Testing Decisions

- Test direct structured reviewer 403 classification at the cursor invocation
  boundary and assert no retry.
- Test Cursor-primary fallback success with compact structured prompt metadata
  and truthful fallback fields.
- Test workflow blocking for `reviewer_access_denied` and assert no degraded
  reviewer-unavailable recovery event.
- Keep existing regression tests for genuine infrastructure degraded recovery,
  real reviewer revise/deny blocking, and Cursor-primary fallback hierarchy.
- Run focused reviewer/workflow tests before the full suite.

## Out Of Scope

- Fixing the external Cursor SDK `internal: internal error` root cause.
- Removing reviewer-unavailable recovery for genuine transient both-down states.
- Weakening typed outcome validation, gate ordering, P-probes, or independent
  reviewer AND algebra.
- Renaming Cursor reviewer abstractions, adding a fourth reviewer, or changing
  `lead_direct` / agentic policy defaults.
