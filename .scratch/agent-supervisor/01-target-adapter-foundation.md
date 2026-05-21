# Issue 01: Add Target Adapter Foundation With Claude Code as v1 Target

## What to build

Introduce the target-agent abstraction that lets the supervisor run against
Claude Code now and Codex later. Add shared normalized types, target-aware
config, adapter selection, and a first Claude Code adapter skeleton. The daemon
should be able to start in `target.kind: claude_code` mode without requiring
Codex paths or commands.

## PRD promise

**Promise IDs:** P1, P2

**User-visible promise:** A user can configure the supervisor for `claude_code`
and run the daemon without Codex dependencies, while Codex remains a future
adapter rather than a core rewrite.

**Public boundary:** `target_config_load`, `target_adapter_conformance`

**Allowed outcomes:** Claude Code adapter starts; missing Codex config is
ignored; unsupported target features return honest degraded states; Codex
adapter can be stubbed behind the same interface.

**Forbidden outcomes:** startup fails because Codex config is missing; drift or
replay imports Codex-only payload fields; hardcoded target commands outside an
adapter.

**Representative prompt/action:** Set `target.kind: claude_code`, load config,
construct the adapter, and run the adapter conformance suite.

## Acceptance criteria

- [ ] Config supports `target.kind: claude_code` and optional `target.codex`.
- [ ] Core startup chooses the adapter by config without importing Codex-only
      modules in the Claude Code path.
- [ ] Shared normalized event and target action types exist.
- [ ] Claude Code adapter passes the initial conformance tests for health,
      hook normalization, and unsupported feature reporting.
- [ ] Codex adapter remains available as a stub or compatibility implementation.

## TDD plan

First public behavior: loading a Claude Code config constructs a working target
adapter without Codex configuration.

RED: Add a `target_config_load` test with no Codex config and assert the selected
adapter is Claude Code and startup does not require `~/.codex`.

GREEN: Add target config models, adapter selection, and a minimal
`ClaudeCodeAdapter`.

Next cycles:

- Add `target_adapter_conformance` tests for hook normalization and healthcheck.
- Add a fake Codex adapter conformance case to prove the boundary is shared.
- Add helper tests for individual normalization edge cases only after the first
  boundary test passes.

## Blocked by

None - can start immediately.
