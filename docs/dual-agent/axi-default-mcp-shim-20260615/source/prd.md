# AXI Default With MCP Compatibility PRD

## Problem Statement

Supervisor workflow instructions still mix MCP-first and AXI/CLI-first
operation. The runtime now relies on a durable ledger plus detached workflow
dispatcher, but some default prompts and help text still point agents at MCP as
the primary whole-workflow path. That creates ambiguity during transport drops,
keeps older retry language alive, and makes it easier for a compatibility tool
to look like the owner of long-running phase execution.

The required product state is simple: AXI/CLI is the default orchestration
surface for supervised workflow submit, poll, and catch-up; MCP remains a thin
native-tool compatibility adapter over the same core API; the detached
dispatcher remains the only process that writes request files or spawns
workflow workers.

## Solution

Update current supervisor-facing docs, skill guidance, recovery hints, and
tests so rigorous workflow instructions use AXI JSON commands by default:
`codex-supervisor-axi --json submit`, `codex-supervisor-axi --json poll`, and
`codex-supervisor-axi --json catch-up`. Keep MCP tools available with the same
public names, but document and test them as non-blocking ledger shims.

The slice preserves existing client-token idempotency, gate authority, reviewer
authority, runtime-native evidence checks, and dispatcher recovery behavior.
JSON remains the automation format. TOON-lite may remain available for human
flat-list output, but this slice does not claim TOON improves supervisor agent
performance.

## User Stories

1. As an operator, I want default rigorous-flow prompts to show AXI JSON
   commands, so that I can recover state without depending on MCP transport.
2. As an agent, I want submit and poll results in JSON, so that retries,
   catch-up cursors, and job IDs are parseable.
3. As a legacy MCP caller, I want MCP tools to remain available, so that old
   integrations do not break while the default operating path moves to AXI.
4. As a runtime maintainer, I want tests proving MCP calls do not execute
   workflow phases inline, so that transport timeouts cannot return through
   poll or compatibility run calls.
5. As a reviewer, I want AXI and MCP retries with the same client token to
   produce one job, so that idempotency remains surface-independent.
6. As an evidence reviewer, I want docs to avoid unsupported TOON or AXI
   performance claims, so that interface choices remain measurement-gated.

## PRD Promise Contracts

P1. AXI JSON default workflow
User-visible promise: Current rigorous-flow docs and skill guidance show AXI
`--json submit`, `--json poll`, and `--json catch-up` as the default durable
workflow path.
Representative prompts or actions: start a supervised implementation; poll to
terminal; recover after a transport drop.
Public boundary: `codex_supervisor_axi`
Allowed outcomes: AXI JSON commands appear as the default; MCP is named only as
compatibility or native-tool access.
Forbidden outcomes: MCP-first whole-workflow instructions; automation examples
without JSON; guidance that says poll executes phases.
Related user stories: 1, 2.

P2. MCP non-blocking compatibility
User-visible promise: MCP submit, poll, catch-up, and compatibility
`run_dual_agent_workflow` reserve or read durable state only and include AXI
recovery hints.
Representative prompts or actions: a legacy MCP tool call starts a workflow; an
MCP poll reads a reserved job; an MCP catch-up reads events after a dropped
transport.
Public boundary: `codex_supervisor_mcp`
Allowed outcomes: durable job response, detached-dispatcher-only metadata, MCP
tool names, and AXI JSON recovery equivalents.
Forbidden outcomes: dispatcher `run_once` from poll; request files written by
MCP poll; subprocess workers spawned by MCP poll; synchronous gate execution in
the compatibility tool.
Related user stories: 3, 4.

P3. Cross-surface idempotency
User-visible promise: AXI and MCP use the same client-token semantics and event
tail, so cross-surface retries reattach to one job and read the same run events.
Representative prompts or actions: AXI submit then MCP submit with one token;
MCP submit then AXI submit with one token; AXI and MCP catch-up for one run.
Public boundary: `codex_supervisor_axi`
Allowed outcomes: exactly one job row; second submit reports reattached;
catch-up IDs, kinds, count, and cursor match.
Forbidden outcomes: duplicate durable jobs for one token; divergent event tails
by interface; catch-up mutates gate state.
Related user stories: 5.

P4. Measurement-safe format policy
User-visible promise: Automation keeps JSON as the exact output format and docs
make no TOON performance claim without local normalized evidence.
Representative prompts or actions: compare AXI versus MCP; choose output
format for agent poll loops.
Public boundary: `codex_supervisor_axi`
Allowed outcomes: TOON-lite described as optional or human-readable; metrics
remain observational.
Forbidden outcomes: TOON promoted as the agent default; AXI claimed to perform
better than MCP from unnormalized incident shares or external-only benchmarks.
Related user stories: 6.

## Implementation Decisions

- Keep `CodexSupervisorMcpAPI` as the shared core behind both AXI and MCP.
- Update AXI help after submit and poll to show JSON-form recovery commands.
- Update MCP compatibility help to include both MCP tool names and AXI JSON
  equivalents.
- Update current operating docs and `skills/dual-agent-gate.md` so new
  workflows start from AXI and use MCP only for compatibility, inspection, or
  low-level gate repair.
- Add public-boundary tests for cross-surface reattach, catch-up equivalence,
  MCP compatibility help, and docs/default prompt drift.
- Preserve all gate semantics and reviewer authority.

## Testing Decisions

- The first tests for P1, P3, and P4 exercise the AXI CLI public entrypoint.
- The first tests for P2 exercise MCP tool/API boundaries and fail if inline
  phase execution is attempted.
- Catch-up equivalence compares stable event fields and proves no writes occur
  from the read path.
- Docs tests target current default guidance, not archived transcripts or old
  workflow artifacts.
- Existing MCP and AXI regression suites remain in scope.

## Out of Scope

- Deleting MCP or removing its public tool names.
- Changing gate order, runtime evidence rules, reviewer panel rules, Cursor
  review policy, Claude Code model policy, or dispatcher leases.
- Making TOON the automation default.
- Claiming AXI is empirically better than MCP without a valid local benchmark.
- Adding a new execution engine.

## Further Notes

This is a boundary migration with tests. The success criterion is that agents
and operators see AXI JSON as the default, MCP remains compatible and
non-blocking, and all long-running execution remains dispatcher-owned.
