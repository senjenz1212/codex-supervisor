# Evidence Status: RUNTIME-001

## Status

**Partial. Runtime contracts exist; repository-wide provider migration is not
complete.**

## Current Repository Evidence

Focused command:

```text
.venv/bin/python -m pytest -q \
  tests/test_agent_runtime.py tests/test_model_client.py \
  tests/test_claude_sdk_runtime.py tests/test_agent_invoker_review.py
```

Observed result: **10 passed**.

This proves common result schemas with fake transports, runtime-level
resume/cancel calls, structured model-response validation, Claude SDK loading
inside its transport, and `AgentInvoker` seam usage.

## Open Evidence Gaps

- `supervisor/drift_detector.py` still imports Anthropic and OpenAI clients.
- `supervisor/hook_critic.py` and `supervisor/telegram_supervisor.py` still
  construct Claude SDK clients.
- `supervisor/cursor_agent.py` still imports Cursor SDK and OpenAI clients in
  provider-specific paths.
- `supervisor/agentic_executor.py` still constructs Claude CLI execution.
- No grep/AST regression currently proves all core model calls use
  `AgentRuntime`/`ModelClient`.
- No real Claude Code or Codex task was run by this documentation task.
- No cancellation-with-descendant proof, external review, skill receipt, or
  commit was produced here.

## Claim Boundary

The current evidence supports provider-neutral interface behavior in focused
tests. It does not support the claim that every model call is routed through
the seam or that both live runtimes execute equivalent tasks successfully.
