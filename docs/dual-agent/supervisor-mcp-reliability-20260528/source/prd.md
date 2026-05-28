# Supervisor MCP Reliability PRD

## Problem Statement

The supervisor workflow has been too dependent on the Codex Desktop MCP
transport. When the app-side MCP session returns `Transport closed`, operators
lose the live tool path even if the supervisor server, SQLite ledger, Cursor
reviewer, and Claude gate runner are otherwise healthy. The result is a brittle
review surface: a transport failure can look like a failed supervisor verdict,
or can block urgent follow-up work that still needs the same governance checks.

## Solution

Make the supervisor review path resilient by separating transport health from
workflow truth. The MCP server should keep stdio protocol output clean, while a
first-class CLI fallback should call the same `CodexSupervisorMcpAPI`
workflow boundary, load the same secrets and Cursor environment, write the same
ledger records, and export the same artifact directory. Cursor participation
should also be configurable by gate profile so a rigorous run can review the
planning and outcome gates without prompt-level guesswork.

## User Stories

1. As an operator, I want `Transport closed` to be diagnosed as transport
   health, so that I do not confuse it with a supervisor rejection.
2. As a reviewer, I want a non-MCP command that runs the same workflow API, so
   that I can still get a durable verdict when Desktop MCP is wedged.
3. As a lead, I want rigorous Cursor review to be selected by policy, so that
   quality gates receive the intended third-reviewer coverage.
4. As a future maintainer, I want documented recovery steps, so that the next
   chat can continue from artifacts instead of stale chat memory.

## PRD Promise Contracts

P1. MCP stdout remains protocol-only
User-visible promise: A direct MCP stdio tools call writes JSON-RPC messages to
stdout and does not leak request logging into the protocol stream.
Representative prompts or actions: Run a JSON-RPC initialize and `check_budget`
tools call against `codex-supervisor-mcp`.
Public boundary: `mcp_tools.codex_supervisor_stdio`
Allowed outcomes: stdout contains valid JSON-RPC response lines; stderr has no
routine request chatter.
Forbidden outcomes: request logs appear on stdout or break a Codex MCP client.
Related user stories: 1

P2. CLI fallback uses the same supervisor workflow
User-visible promise: `uv run codex-supervisor-workflow` calls
`CodexSupervisorMcpAPI.run_dual_agent_workflow`, writes a JSON result, records
ledger state, and exports `docs/dual-agent/<task_id>/` artifacts.
Representative prompts or actions: Run the fallback with a JSON request after
an MCP `Transport closed` error.
Public boundary: `mcp_tools.codex_supervisor_workflow_cli`
Allowed outcomes: accepted, blocked, or failed supervisor verdicts are durable
and inspectable.
Forbidden outcomes: fallback uses a weaker review path or returns only chat
text with no ledger/artifacts.
Related user stories: 2 and 4

P3. Cursor gate coverage is policy-driven
User-visible promise: Default Cursor review covers the final outcome gate,
rigorous review covers TDD, implementation plan, and outcome gates, and vague
work covers early planning quality gates.
Representative prompts or actions: Call `run_dual_agent_workflow` with
`cursor_review_profile="rigorous"` or `task_complexity="vague"`.
Public boundary: `supervisor.dual_agent_workflow.cursor_review_gates_for_workflow`
Allowed outcomes: selected gates are recorded in the workflow route and Cursor
events appear only for those gates.
Forbidden outcomes: Cursor silently reviews every gate, no gate, or a prompt-
determined set that is not visible in policy.
Related user stories: 3

P4. Recovery instructions are durable
User-visible promise: The new-chat how-to describes MCP as primary, CLI as
fallback, and artifact-only review as the last resort.
Representative prompts or actions: Open the how-to after a transport failure.
Public boundary: `docs/how-to/dual-agent-from-new-chat.md`
Allowed outcomes: the operator can run a fallback request and know when to
restart Desktop.
Forbidden outcomes: guidance claims artifact-only review is equivalent to a
live supervisor run.
Related user stories: 4

## Implementation Decisions

- Keep the existing MCP tool API as the primary path.
- Lower MCP transport logging at server construction and pass `log_level=ERROR`
  when the current FastMCP implementation supports it.
- Add a console script named `codex-supervisor-workflow` for fallback review.
- Load `[mcp_servers.codex_supervisor.env]` from `~/.codex/config.toml` before
  `~/.codex-supervisor/secrets.env`, without overriding variables already set
  in the shell.
- Route Cursor review through deterministic gate-selection policy.

## Testing Decisions

Tests cover the real stdio subprocess boundary, the fallback workflow function,
the Codex MCP env loader, Cursor gate profile selection, and the existing
workflow driver behavior. The local verification also includes compileall,
`git diff --check`, and a CLI smoke run that produces a legitimate supervisor
blocked verdict for missing receipts.

## Out of Scope

This slice does not repair a stale Codex Desktop MCP client process from inside
the Python server. Restarting Desktop remains the recovery action for a wedged
app-side MCP session. This slice also does not commit unrelated live Cursor
probe artifacts or scratch database files that predate the reliability change.
