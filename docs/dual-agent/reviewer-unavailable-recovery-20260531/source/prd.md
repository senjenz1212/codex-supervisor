# Reviewer Unavailable Recovery PRD

## Problem Statement

PR #2 correctly classifies malformed or unavailable Cursor reviewer output as
`reviewer_contract_unmet` or `reviewer_infrastructure_unavailable`, but the
workflow still treats those recoverable infrastructure states as a terminal gate
block. A run can have valid Claude and Codex acceptance, durable Cursor failure
evidence, and no real reviewer rejection, yet the workflow cannot reach the next
gate or `outcome_review`.

## Solution

Add an explicit `reviewer_unavailable_policy` for the dual-agent workflow. The
policy keeps hard-block behavior available, makes the default path a resumable
human escalation, and allows an explicit degraded-forward mode only when the
available reviewers accept and the unavailable reviewer is recorded as degraded
infrastructure evidence rather than acceptance.

## User Stories

1. As an operator, I want reviewer infrastructure failures to escalate by
   default, so a missing Cursor verdict does not become a dead-end block.
2. As an auditor, I want a degraded reviewer-unavailable receipt in the ledger,
   so I can prove the missing verdict was not counted as an acceptance.
3. As a gate owner, I want explicit `block` mode, so PR #2's conservative
   behavior remains available.
4. As a quality reviewer, I want genuine Cursor `revise` or `deny` decisions to
   keep blocking, so recovery never weakens real independent review.
5. As a safety owner, I want high-stakes and runtime-native evidence gates to
   escalate instead of auto-proceeding, so degraded review requires human
   authorization.

## PRD Promise Contracts

P1. Reviewer-unavailable policy is explicit
User-visible promise: `run_dual_agent_workflow` accepts
`reviewer_unavailable_policy` in `{block, escalate, proceed_degraded}`, with
default `escalate` from config.
Representative prompts or actions: Run a workflow with Cursor review enabled
and omit the policy argument.
Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
Allowed outcomes: default policy is `escalate`; CLI payloads preserve explicit
policy values; `lead_direct` stays default.
Forbidden outcomes: hidden prompt-only behavior or a default that silently
auto-proceeds degraded.
Related user stories: 1, 3

P2. Proceed-degraded advances only on available reviewer acceptance
User-visible promise: With `proceed_degraded`, a recoverable Cursor
infrastructure failure can advance only when Claude accepts and Codex's
deterministic checks accept.
Representative prompts or actions: Run `run_dual_agent_workflow` with a Cursor
contract-miss fixture and valid Claude/Codex acceptance.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the workflow advances to later gates and reaches
`outcome_review`; ledger records degraded reviewer-unavailable evidence.
Forbidden outcomes: the missing Cursor verdict is counted as accept, or the
workflow advances when Claude/Codex did not accept.
Related user stories: 2

P3. Default escalation is resumable
User-visible promise: With default `escalate`, reviewer infrastructure failure
creates a human-resumable escalation rather than a terminal dead end.
Representative prompts or actions: Run a workflow, authorize `Continue` through
the existing resume signal machinery, then rerun the workflow.
Public boundary: workflow ledger actions plus `run_dual_agent_workflow`
Allowed outcomes: first run pauses for human; a continue resume lets the same
gate proceed degraded with durable evidence.
Forbidden outcomes: returning a blocked result with no resume action, or
requiring a fake Cursor verdict to continue.
Related user stories: 1, 2

P4. Block mode preserves PR #2 behavior
User-visible promise: With `reviewer_unavailable_policy=block`, the workflow
still returns the existing blocked gate result.
Representative prompts or actions: Run a Cursor contract-miss fixture in block
mode.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: blocked gate override and no advancement.
Forbidden outcomes: block mode silently escalates or proceeds degraded.
Related user stories: 3

P5. Real reviewer rejection still blocks
User-visible promise: A valid Cursor `revise` or `deny` verdict continues to
block under all reviewer-unavailable policies.
Representative prompts or actions: Run a valid Cursor `revise` fixture.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the gate blocks as a real quality rejection.
Forbidden outcomes: infrastructure recovery treats a real reviewer rejection as
unavailable.
Related user stories: 4

P6. High-stakes degraded auto-proceed is gated
User-visible promise: `proceed_degraded` cannot auto-fire for agentic-required,
runtime-native required, or user-facing/high-stakes evidence paths.
Representative prompts or actions: Run with `agentic_lead_policy=required` or
`required_evidence_grade=runtime_native` and a Cursor contract miss.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the workflow escalates to a human instead of auto-proceeding.
Forbidden outcomes: degraded auto-proceed on high-stakes evidence.
Related user stories: 5

## Implementation Decisions

- Add `reviewer_unavailable_policy` to config and workflow inputs.
- Keep Cursor downstream of accepted Claude gate results.
- Reuse Cursor's existing recoverable classification fields.
- Record reviewer-unavailable recovery as supervisor-derived ledger evidence.
- Keep real Cursor `revise` and `deny` on the existing AND-verdict path.
- Treat degraded review as a distinct evidence grade, not as reviewer accept.

## Testing Decisions

The first RED tests exercise `run_dual_agent_workflow`, because the regression
is the workflow branch that returns a blocked result after Cursor infrastructure
classification. CLI payload preservation and transcript/ledger evidence are
covered at the same public boundaries.

## Out of Scope

This slice does not fix Cursor prompt quality, change Cursor model selection,
alter P1/P2/P3/P13/P14 semantics, build durable MCP transport, add a fourth
reviewer, or change the default `lead_direct` execution mode.
