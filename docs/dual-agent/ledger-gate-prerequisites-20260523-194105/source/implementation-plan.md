# Implementation Plan

## Scope

- Add gate prerequisite constants to the Codex-facing MCP server.
- Extend strict preflight to inspect ledger gate status for the current run and task.
- Block before launching Claude Code when required prerequisite gates are missing.
- Include prerequisite details in `artifact_rigor`.
- Add `issues_review` to the handoff gate literal.
- Render prerequisite details in Markdown exports.
- Update dual-agent skill docs and tests.

## Out Of Scope

- Changing Telegram resume behavior.
- Changing Claude Code `/lead` internals.
- Adding a new UI cockpit.
