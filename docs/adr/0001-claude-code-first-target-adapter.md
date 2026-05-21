# ADR 0001: Supervise Claude Code First Through a Target Adapter

## Status

Accepted

## Context

The original design was Codex-first. The product requirement changed: the
supervisor must work with Claude Code now, while preserving a future path to
Codex. The existing scaffold has Codex-specific names, rollout ingestion, hook
routes, and MCP tools. A direct rename or rewrite would slow the current build
and make the next target migration harder.

## Decision

Keep the repository name and existing scaffold, but move all target-specific
behavior behind a `TargetAgentAdapter` interface.

Claude Code is the v1 adapter. Codex is a future adapter. Supervisor core code
may consume only normalized events, scope contracts, hook events, target actions,
and decision-runtime results.

## Consequences

- The first shippable path can focus on Claude Code without deleting the Codex
  prototype work.
- Codex support becomes an adapter implementation and conformance-test problem,
  not a core rewrite.
- Some existing modules will keep transitional names until the target boundary
  is stable.
- Tests must enforce that drift, replay, action policy, and runtime decisions do
  not read raw Claude Code or Codex payloads directly.
