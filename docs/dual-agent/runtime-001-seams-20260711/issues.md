# Issues: RUNTIME-001

## RT1: Stable AgentRuntime contract

- PRD promises: P1, P2, P3
- Public boundary: `AgentRuntime`
- Blocked by: INTEGRITY-B cancellation semantics
- Acceptance:
  - Claude Code and Codex produce the same result keys and event taxonomy.
  - Resume/cancel/stream remain behind the runtime seam.
  - Capability declarations are recorded on the handle.

## RT2: Provider transports

- PRD promises: P2, P3, P6
- Public boundary: `ClaudeCodeRuntime` and `CodexRuntime`
- Blocked by: RT1
- Acceptance:
  - CLI/SDK details stay in transport adapters.
  - Session IDs, timeouts, costs, terminal status, and resolved model are
    normalized.
  - Descendant processes do not survive cancellation.

## RT3: ModelClient

- PRD promises: P4
- Public boundary: `ModelClient`
- Blocked by: none
- Acceptance:
  - Completion returns one provider-neutral response.
  - Structured completion validates against the requested schema.
  - Provider and resolved model are mandatory evidence.

## RT4: Migrate provider call sites

- PRD promises: P5
- Public boundary: application composition roots
- Blocked by: RT1, RT3
- Acceptance:
  - `AgentInvoker`, Telegram, hook critic, agentic execution, Cursor review,
    drift adjudication, planning, and recovery use injected seams.
  - A source-scan regression rejects direct provider SDK imports in core
    modules.
  - Provider adapters remain importable without optional SDKs installed.
