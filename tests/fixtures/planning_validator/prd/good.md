# Good PRD Fixture

## Problem Statement

Dual-agent gates can currently accept source artifacts that look like a PRD but
do not contain product decisions. The workflow needs deterministic protection
against stubs before the worker receives a handoff packet.

## Solution

Validate the PRD before worker invocation. The validator checks sections,
promise contracts, concrete acceptance language, and replayable anti-template
signals without calling a model.

## User Stories

1. As an operator, I want stub PRDs to block, so that fake planning cannot ship.
2. As a reviewer, I want promise IDs to be stable, so that issues and tests can reference them.
3. As a future chat, I want receipts, so that I can reconstruct why the gate stopped.

## PRD Promise Contracts

P1. Stub PRDs block
User-visible promise: A placeholder PRD returns a blocked planning validation before Claude runs.
Representative prompts or actions: Start a gate with generated source docs only.
Public boundary: dual_agent_runner
Allowed outcomes: blocked with PRD check failures; accepted only with substantive content.
Forbidden outcomes: Claude runs on placeholders or section-only text.
Related user stories: 1

P2. Promise traceability survives
User-visible promise: Downstream plans can reference real PRD promise IDs.
Representative prompts or actions: Implementation plan references P1 and P2.
Public boundary: dual_agent_planning_validator
Allowed outcomes: references to known promise IDs pass.
Forbidden outcomes: fabricated promise references pass.
Related user stories: 2

P3. Receipts explain validation
User-visible promise: A later transcript shows which PRD checks passed or failed.
Representative prompts or actions: Read a gate transcript after a blocked planning gate.
Public boundary: codex_supervisor_mcp
Allowed outcomes: receipt includes validator version, hashes, and check IDs.
Forbidden outcomes: the reason only exists in chat scrollback.
Related user stories: 3

## Implementation Decisions

- Add a pure planning validator module.
- Run validation in the runner before `/lead`.
- Persist receipts through the existing event ledger.

## Testing Decisions

Tests start at `dual_agent_runner`, `codex_supervisor_mcp`, and
`dual_agent_planning_validator`. The first RED proof blocks before the fake
runner is called.

## Out of Scope

Operator-level waivers, live Claude probes, live Cursor probes, and LLM-based
quality scoring are out of scope for this fixture.
