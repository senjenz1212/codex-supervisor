# Cursor SDK Realistic Prompt Probe

## Result

The realistic Cursor SDK probe forced `reviewer_output_mode="cursor_sdk"`,
loaded `CURSOR_API_KEY` through the same Codex MCP config path as the workflow
CLI, and built a reviewer prompt from the requested intent, prior reviewer
reliability planning artifacts, prior outcome JSON, and receipt context.

The prompt was 23662 characters with 9 planning artifacts and 3 receipts. The
minimal forced Cursor SDK probe succeeded earlier, but this realistic prompt
probe did not return within the requested 180 second envelope. Because the
current SDK invocation path does not enforce `CursorInvocationRequest.timeout_s`
around `run.text()` / `wait()`, the parent process had to be killed externally.

## Interpretation

Cursor is viable enough to keep as the primary agentic reviewer, but not safe
enough to make primary without this slice's reliability controls. The
implementation must add supervisor-side timeout enforcement, per-attempt
diagnostics, prompt-size capture, raw run metadata, and an honestly degraded
Gemini fallback.

## Evidence

- Minimal forced Cursor SDK probe: `/tmp/codex-supervisor-current-cursor-sdk-forced/summary.json`
- Realistic prompt summary:
  `docs/dual-agent/independent-reviewer-cursor-primary-20260531/phase0/cursor-sdk-realistic-prompt/summary.json`
