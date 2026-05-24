# PRD: Dual-Agent From A New Chat How-To

## Problem

The dual-agent system can be resumed from another chat, but the operator needs exact setup commands, prompts, artifact paths, and failure handling. Without a checked-in how-to, each new chat risks relying on memory or partial instructions.

## Goal

Document how to start, continue, or inspect a dual-agent run from another Codex chat.

## Acceptance Criteria

- Explain what state carries across chats.
- Include the `codex_supervisor` MCP registration command.
- Include prompts for continuing an existing run and starting a fresh run.
- Document the strict gate sequence and planning artifact payload.
- Point operators at `interactions.md` as the primary readable artifact.
- Explain non-MCP fallback as artifact-only review.
- Cover common failure modes.
