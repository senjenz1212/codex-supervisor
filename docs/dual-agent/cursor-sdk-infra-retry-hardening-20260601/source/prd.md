# Cursor SDK Infrastructure Retry Hardening PRD

## Problem Statement

The supervisor currently treats a single Cursor SDK infrastructure exception as
`reviewer_infrastructure_unavailable`. During the agentic detached fan-out run
on June 1, 2026, seven Cursor reviewer attempts failed in one cluster with
`internal: internal error`, while later live probes against the same repo and
the same review packet succeeded. The failure was transient, but the supervisor
had no bounded retry/backoff path before falling into reviewer-unavailable
recovery.

This creates avoidable human interruptions. Cursor is still the independent
reviewer, and missing verdicts must never count as acceptance, but the system
should absorb short SDK/bridge flakes before escalating to the operator.

## Solution

Add a bounded Cursor SDK infrastructure retry path inside
`supervisor.cursor_agent.invoke_cursor_agent`. The retry path applies only to
infrastructure failures from the Cursor SDK boundary, not to typed Cursor
`revise`/`deny` outcomes and not to malformed review contracts. Each retry keeps
the original review packet, records attempt metadata, and waits with configured
backoff before retrying.

Thread the retry limit and backoff through supervisor configuration and workflow
payloads so rigorous gates can tune the reliability behavior without changing
gate semantics. Preserve the existing fallback and reviewer-unavailable recovery
after retries are exhausted.

## User Stories

1. As an operator, I want transient Cursor SDK bridge errors retried before the
   gate asks me to authorize degraded progress, so short-lived reviewer outages
   do not interrupt long supervised runs.
2. As an auditor, I want retry attempts, final error, and prompt diagnostics
   recorded in the reviewer result, so I can distinguish a flaky SDK call from a
   genuine Cursor quality objection.
3. As a gate maintainer, I want valid Cursor `revise` or `deny` outcomes to keep
   blocking exactly as before, so reliability work does not weaken independent
   review.
4. As a workflow owner, I want retry policy to be configurable and threaded
   through CLI/MCP submit paths, so detached jobs and inline runs behave
   consistently.
5. As a safety reviewer, I want fallback and reviewer-unavailable recovery to
   run only after bounded SDK retries fail, and I want missing Cursor verdicts
   to remain non-accepting evidence.

## PRD Promise Contracts

P1. Cursor SDK infrastructure failures retry before fallback
User-visible promise: A transient Cursor SDK infrastructure exception is retried
under a bounded policy before fallback or reviewer-unavailable recovery runs.
Representative prompts or actions: Invoke `invoke_cursor_agent` with a Cursor
SDK runner that raises once, then returns a valid typed outcome.
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Allowed outcomes: the second attempt succeeds, `cursor_review_ok` is returned,
and retry diagnostics show one infrastructure retry.
Forbidden outcomes: the first SDK exception immediately marks Cursor
unavailable or triggers fallback.
Related user stories: 1, 5

P2. Exhausted infra retries remain typed unavailable evidence
User-visible promise: If all Cursor SDK infrastructure attempts fail, the result
is still a typed `reviewer_infrastructure_unavailable` failure with attempt
history and no fabricated reviewer outcome.
Representative prompts or actions: Invoke `invoke_cursor_agent` with a Cursor
SDK runner that always raises.
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Allowed outcomes: the result is recoverable, includes retry count/error history,
and then follows existing fallback behavior.
Forbidden outcomes: missing Cursor verdict is counted as accept, retry metadata
is lost, or fallback runs before the configured attempts are exhausted.
Related user stories: 2, 5

P3. Contract retries remain separate from infrastructure retries
User-visible promise: Malformed Cursor output continues to use the existing
contract-corrective retry path, not the SDK infrastructure retry budget.
Representative prompts or actions: Invoke `invoke_cursor_agent` with a Cursor
SDK runner that returns text without `<dual_agent_outcome>`.
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Allowed outcomes: the corrective contract packet is sent, contract retry counts
are reported, and no infrastructure fallback is attempted for contract misses.
Forbidden outcomes: malformed output consumes infrastructure retry budget or
falls back as if the SDK transport failed.
Related user stories: 2, 3

P4. Workflow routes carry reviewer retry policy
User-visible promise: `run_dual_agent_workflow` and detached workflow submit
accept reviewer infrastructure retry settings and pass them to Cursor
invocation requests.
Representative prompts or actions: Submit a workflow payload with
`reviewer_infra_retry_limit` and `reviewer_infra_retry_backoff_s`.
Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`,
`submit_dual_agent_workflow_job`, and `mcp_tools.codex_supervisor_workflow_cli`
Allowed outcomes: route metadata and `CursorInvocationRequest` carry the
configured values.
Forbidden outcomes: inline and detached runs use different retry policies, or
unknown payload fields are silently discarded.
Related user stories: 4

P5. Real Cursor review decisions keep AND-gate semantics
User-visible promise: A valid Cursor `revise` or `deny` still blocks the gate;
retry hardening only covers infrastructure exceptions before a verdict exists.
Representative prompts or actions: Run a workflow with Claude accept and Cursor
returning a valid `revise`.
Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
Allowed outcomes: the gate blocks as before with Cursor's substantive objection.
Forbidden outcomes: retry policy downgrades a real Cursor rejection into
degraded progress or acceptance.
Related user stories: 3, 5

## Implementation Decisions

- Add retry fields to `CursorInvocationRequest` with safe defaults.
- Retry only Cursor SDK infrastructure exceptions and watchdog timeouts.
- Do not retry missing Python modules, because retry cannot repair an
  installation/configuration absence inside the same process.
- Keep fallback to `litellm_structured` after SDK infrastructure retries are
  exhausted and only when an explicit fallback key is supplied.
- Add compact diagnostics: infrastructure attempt count, per-attempt reason,
  final error, backoff sequence, prompt hash, and prompt character count when
  available.
- Thread policy through `Config.supervisor`, workflow CLI payload filtering,
  inline workflow calls, and detached job payloads.
- Keep defaults compatible: Cursor remains enabled only when the workflow asks
  for Cursor review, and missing verdicts remain non-accepting evidence.

## Testing Decisions

The first RED tests exercise `supervisor.cursor_agent.invoke_cursor_agent`
because the diagnosed failure occurs inside the Cursor invocation boundary.
Workflow-level tests then prove the policy is threaded through
`run_dual_agent_workflow` and CLI payload parsing. Regression tests preserve
existing contract retry, fallback, and Cursor rejection semantics.

## Out of Scope

- Changing the default Cursor model.
- Adding Gemini or another fourth reviewer.
- Replacing Cursor SDK with another transport.
- Changing `reviewer_unavailable_policy` semantics.
- Auto-accepting or hiding missing Cursor verdicts.
- Fixing Tessen or VPN access for structured fallback.
