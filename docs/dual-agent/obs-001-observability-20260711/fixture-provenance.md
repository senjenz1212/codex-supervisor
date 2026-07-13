# Captured Fixture Provenance

## Codex

`tests/fixtures/rollout_watcher/codex_nested_terminal.jsonl` was reduced from a
local Codex rollout captured on July 11, 2026. It preserves the observed
`session_meta`, `event_msg.payload.type`, and `response_item.payload.type`
structure and representative lifecycle order.

## Claude Code

`tests/fixtures/rollout_watcher/claude_nested_terminal.jsonl` was reduced from a
local Claude Code project transcript captured on July 11, 2026. It preserves
top-level `user` / `assistant` entries, nested `message.content` blocks, tool
use/results, and `message.stop_reason=end_turn`.

## Sanitization

- Session/message/tool IDs were replaced with fixture IDs.
- Workspace paths were replaced with `/captured/workspace`.
- User and assistant text was replaced with minimal behavioral text.
- Token values were minimized.
- Encrypted content, prompts, secrets, environment values, and repository
  content were omitted.
- Event wrapper names, nesting, status fields, and terminal fields were not
  flattened or renamed.
