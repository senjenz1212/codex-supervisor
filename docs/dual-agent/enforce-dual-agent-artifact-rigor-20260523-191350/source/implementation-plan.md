# Implementation Plan

## Scope

- Extend the Codex-facing MCP API with strict artifact preflight parameters.
- Add preflight helpers that classify required planning artifacts and screenshot evidence.
- Write blocked preflight results to the supervisor ledger.
- Auto-export artifacts for accepted, blocked, resumed, and recorded-round paths.
- Extend planning artifact kinds for grill findings and issues.
- Render artifact rigor details in generated Markdown.
- Update skill docs and tests.

## Out Of Scope

- Replacing the supervisor ledger.
- Changing Claude Code `/lead` internals.
- Adding Telegram-specific behavior.
- Adding a new cockpit UI.
