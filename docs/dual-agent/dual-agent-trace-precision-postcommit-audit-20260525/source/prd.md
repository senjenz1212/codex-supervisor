# PRD: Trace Precision Post-Commit Tri-Agent Audit

## Problem Statement

Commit `f9a6f81 Harden dual-agent trace precision` was implemented locally by
Codex, then validated through probes. The user explicitly asked whether the
implementation itself used the tri-agent approach. The honest answer is that
the implementation was Codex-local, so the project now needs a post-commit
tri-agent audit that makes Claude Code, Cursor, and Codex each inspect the
actual committed diff and record their conclusions in the supervisor ledger.

## Solution

Run a live `outcome_review` gate against the current repository using the
existing dual-agent supervisor. Claude Code receives a review prompt grounded in
commit `f9a6f81`, the planning artifacts, and the trace-precision promises.
Cursor then performs an independent read-only review of the same target and
Claude's outcome. Codex writes the final audit verdict, exports replay-grade
artifacts, and only patches code if either reviewer finds a concrete
correctness issue.

## User Stories

- As the user, I can open the audit artifact directory and see exactly what
  Claude Code concluded about the trace-precision commit.
- As the user, I can compare Cursor's independent review with Claude's outcome
  and see whether Cursor accepted, rejected, or raised objections.
- As the user, I can inspect Codex's final verdict and verify whether it was
  based on Claude, Cursor, local validation, and artifact hygiene checks.
- As the user, I can see that planning artifacts were substantive enough to pass
  the deterministic validator before any live reviewer was invoked.

## PRD Promise Contracts

P1. Claude Code post-commit review is captured.
Representative action: run a live `outcome_review` gate with a prompt naming
commit `f9a6f81` and the trace-precision files under review. Public boundary:
`supervisor.dual_agent_runner.run_dual_agent_gate`. Allowed outcome: Claude
accepts or denies with structured claims, confidence rationale, and evidence
refs. Forbidden outcome: the audit claims Claude participated when the gate was
blocked before Claude invocation.

P2. Cursor independent review is captured.
Representative action: invoke Cursor after Claude returns a typed outcome,
passing the same review target and Claude outcome JSON. Public boundary:
`supervisor.cursor_agent.invoke_cursor_agent`. Allowed outcome: Cursor accepts
or rejects with a persisted `tri_agent_cursor_review` event and transcript tail.
Forbidden outcome: Cursor is skipped without the artifact naming the reason.

P3. Codex final verdict preserves the full trace.
Representative action: write a final `dual_agent_gate_result` and export the
run. Public boundary:
`supervisor.dual_agent_artifacts.export_dual_agent_run_artifacts`. Allowed
outcome: `summary.json`, `transcript.jsonl`, `interactions.md`, `triage.md`,
and replay manifest link the Claude, Cursor, and Codex events. Forbidden
outcome: only prose notes exist with no ledger-backed event chain.

P4. Audit hygiene is verified before commit.
Representative action: run local validation, compile checks, diff hygiene, and
secret scans over generated artifacts. Public boundary: repository command
line and git diff. Allowed outcome: failures are fixed or documented. Forbidden
outcome: raw API keys or unrelated untracked files are staged with the audit.

## Implementation Decisions

- Use explicit `PlanningArtifact` paths rather than introducing a new task
  configuration or path convention.
- Keep the audit read-only with respect to production code unless Claude or
  Cursor identifies a specific correctness defect.
- Preserve the first blocked planning-validation attempt as context inside the
  same artifact directory only if the successful export supersedes it clearly.
- Treat Cursor as an independent challenger and Codex/supervisor as the final
  acceptance boundary.

## Testing Decisions

- The source packet itself must pass `validate_planning_artifacts` for
  `gate="outcome_review"` before live reviewers run.
- The audit run must export a machine-readable `transcript.jsonl` with
  `dual_agent_interaction_message`, `tri_agent_cursor_review`, and final
  `dual_agent_gate_result` events when all reviewers run successfully.
- Local verification must include the focused supervisor tests affected by
  trace precision plus a full compile pass over supervisor, MCP tools, scripts,
  and tests.
- Generated artifacts must be scanned for secret-shaped strings before staging.

## Out of Scope

- Reworking the trace-precision implementation without a concrete audit finding.
- Adding Telegram workflow control, visual cryptographic receipts, or new MAST
  detection rules in this post-commit audit slice.
- Treating the Cursor review as a replacement for the deterministic supervisor
  gates or Codex's final responsibility.
