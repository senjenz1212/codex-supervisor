# Cursor Review Reliability PRD

## Problem Statement

The tri-agent workflow treats Cursor as an independent reviewer, but a Cursor run that returns no parseable `<dual_agent_outcome>` currently looks like a gate-quality failure. Operators cannot tell whether Cursor genuinely rejected a gate or whether the reviewer infrastructure failed to honor the typed contract. This creates limbo: the gate blocks even when Claude and deterministic probes are valid, and a later MCP `Transport closed` can make the reviewer failure harder to inspect.

## Solution

Harden the Cursor reviewer boundary so every review either returns a normal typed outcome or a deterministic, typed infrastructure verdict. Cursor still reviews downstream of Claude and Codex probes. Real Cursor `revise` or `deny` decisions continue to block. Contract misses are retried with a corrective packet, then classified as `reviewer_infrastructure_unavailable` or `reviewer_contract_unmet` with persisted ledger evidence and an explicit recovery policy.

## User Stories

1. As an operator, I want malformed Cursor output to be retried with the exact outcome contract, so transient contract misses do not derail a gate.
2. As a gate maintainer, I want terminal reviewer contract misses classified as infrastructure failures, so they are not confused with substantive reviewer objections.
3. As a reviewer, I want genuine Cursor `revise` or `deny` outcomes to keep blocking, so reliability work does not weaken independent review.
4. As an auditor, I want the reviewer verdict or infrastructure failure persisted before transcript export, so `Transport closed` on a later read cannot erase the reason a gate advanced or blocked.
5. As a workflow owner, I want recovery policy to be explicit and bounded, so missing verdicts are never counted as acceptance and gates are never bypassed silently.

## PRD Promise Contracts

P1. Cursor contract misses retry with corrective prompt
User-visible promise: When Cursor output lacks a parseable typed outcome, the supervisor retries the reviewer with a corrective packet that restates the exact `<dual_agent_outcome>` JSON contract.
Representative prompts or actions: Run a Cursor review where the first SDK response omits the outcome block.
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Allowed outcomes: retry attempts are visible in the result metadata and a later valid outcome is used normally.
Forbidden outcomes: a single malformed Cursor transcript immediately becomes an indistinct gate rejection.
Related user stories: 1

P2. Terminal contract miss is typed infrastructure evidence
User-visible promise: After bounded retries, a malformed Cursor review produces a deterministic typed infrastructure result rather than an unclassified red probe.
Representative prompts or actions: Simulate repeated Cursor transcripts with no typed outcome.
Public boundary: `supervisor.cursor_agent`
Allowed outcomes: the result reason is `reviewer_contract_unmet` or `reviewer_infrastructure_unavailable`, includes retry count and transcript refs, and has no fabricated reviewer outcome.
Forbidden outcomes: the supervisor hand-authors a fake Cursor acceptance, treats the missing verdict as a real `revise`, or loses retry evidence.
Related user stories: 2, 5

P3. Real Cursor decisions keep AND-verdict semantics
User-visible promise: A valid Cursor `revise`, `deny`, or blocking critical review still blocks the gate exactly as before.
Representative prompts or actions: Run an accepted Claude gate followed by a valid Cursor review that returns `revise`.
Public boundary: `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`
Allowed outcomes: the workflow round records Cursor's substantive decision and Codex refuses to advance.
Forbidden outcomes: infrastructure recovery mode weakens or ignores a valid Cursor quality rejection.
Related user stories: 3, 5

P4. Gate recovery distinguishes infrastructure from quality
User-visible promise: The gate policy classifies reviewer infrastructure failures separately from reviewer quality decisions and applies only the documented recovery path.
Representative prompts or actions: Run `run_dual_agent_workflow` with Cursor review enabled and a reviewer contract-miss fixture.
Public boundary: `mcp_tools.codex_supervisor_stdio`
Allowed outcomes: the gate records the infrastructure classification and either retries/falls back under policy or escalates deterministically.
Forbidden outcomes: missing verdict is counted as accept, or the gate advances without a documented recovery mode.
Related user stories: 2, 5

P5. Reviewer evidence is durable across transcript transport failures
User-visible promise: Once Cursor review returns a typed verdict or typed infrastructure failure, the verdict is written to the ledger before export/read operations.
Representative prompts or actions: Persist a Cursor review result, then simulate a transcript read failure.
Public boundary: `read_gate_transcript` and state ledger events
Allowed outcomes: the persisted `tri_agent_cursor_review` or interaction event still contains the reviewer classification.
Forbidden outcomes: a live MCP read failure erases or changes the reviewer decision state.
Related user stories: 4

## Implementation Decisions

- Keep Cursor downstream of Claude gate acceptance and deterministic probes.
- Retry malformed Cursor output inside `invoke_cursor_agent` with a bounded corrective prompt.
- Add an explicit infrastructure classification without inventing a successful reviewer outcome.
- Preserve existing `cursor_accepts` behavior for valid Cursor outcomes.
- Write reviewer metadata and infrastructure classification into the existing ledger events used by transcript export.
- Keep raw MCP auto-reconnect, model selection changes, and fourth-reviewer registry out of this slice.

## Testing Decisions

Tests start at `supervisor.cursor_agent.invoke_cursor_agent` for retry/classification, then cover `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow` for gate semantics and `read_gate_transcript` for durable evidence. The first RED proof uses a fake Cursor SDK runner that omits `<dual_agent_outcome>` before returning a valid response, because the operator-facing failure is at the reviewer invocation boundary.

## Out of Scope

This slice does not change P1/P2/P3/P13/P14 semantics, does not auto-accept missing reviewer verdicts, does not change the default Cursor model, does not build transport auto-reconnect, does not add Gemini or another reviewer, and does not alter agentic worker execution.
