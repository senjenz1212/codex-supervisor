# ADR: Independent Reviewer Runtime Boundary

Date: 2026-05-31

## Status

Accepted; default runtime amended 2026-07-11 (see Amendment)

## Context

The supervisor historically exposed the downstream challenger as
`cursor_review`, but the implementation could route that reviewer through
either Cursor SDK or a structured LiteLLM/OpenAI-compatible reviewer. That made
the trace misleading: a Gemini/LiteLLM text-only review could look like a Cursor
agent with codebase tools.

The Cursor SDK path is the higher-assurance default because it can run as a
codebase-aware reviewer in the local worktree. The structured LiteLLM path stays
available as a lower-assurance fallback or explicit compatibility mode.

## Decision

- Default `reviewer_output_mode` is `cursor_sdk`.
- Keep `cursor_review` as the legacy artifact key.
- Add `independent_reviewer` as the truthful boundary alias for new consumers.
- Record `reviewer_runtime`, `reviewer_output_mode`, `reviewer_assurance`, and
  fallback metadata on reviewer payloads and tool-call receipts.
- Treat Cursor SDK timeout, transport failure, or contract failure as
  recoverable reviewer infrastructure, then fall back to structured LiteLLM only
  when OpenAI-compatible credentials are configured.
- Mark fallback reviews as lower assurance. A valid fallback revise or deny
  remains a blocking reviewer verdict.

## Consequences

The default path now exercises Cursor SDK instead of Gemini/LiteLLM. Operators
without `CURSOR_API_KEY` will see reviewer-unavailable recovery rather than an
implicit Gemini review. This is noisier, but honest: the system no longer
pretends a text-only model had codebase-agent capabilities.

## Amendment (2026-07-11)

The direct-Anthropic routing change amends the default: `reviewer_output_mode`
now defaults to `litellm_structured` with a non-Anthropic reviewer model
(default `gpt-5.5`), keeping the independent structured reviewer on Unity
LiteLLM while Claude lanes route directly through Anthropic. Configuration that
pairs `litellm_structured` with a `claude-*` model is rejected. `cursor_sdk`
remains available as an explicit higher-assurance mode, and the runtime
provenance fields from this ADR are unchanged.
