# Tri-Agent Traceability Receipts PRD

## Problem Statement

The dual-agent workflow now owns gate order, but the audit layer still lets important claims depend on agent prose and caller-supplied strings. A future chat can see final outcomes and some rounds, but it cannot reliably reconstruct which evidence receipt justified "tests passed", "implemented", "pushed", or "visual review passed". The tri-agent loop needs a durable, typed receipt trail so Codex, Claude Code, Cursor, and the human can inspect why a confidence score or gate decision was assigned.

## Solution

Add receipt-backed traceability to the workflow layer. Interaction messages should carry first-class claims, objections, questions, evidence references, tool receipts, raw transcript references, persona ids, and "would change if" criteria. Outcome review should require matching tool receipts for verification-sensitive claims instead of trusting outcome fields alone. Transcript and artifact exports should show those receipts and confidence rationales. Fresh-chat resume prompts should include enough state to continue without reconstructing from prose.

## User Stories

1. As Sam reviewing a tri-agent run, I want every Codex, Claude Code, and Cursor message to include its claims, objections, evidence, and confidence criteria, so that I can audit why the agents converged.
2. As Codex resuming a blocked workflow in a fresh chat, I want the supervisor resume prompt to include the current gate, step history, latest event id, blockers, and transcript/artifact commands, so that I continue safely without guessing.
3. As a reviewer, I want "tests passed", "implemented", "pushed", and "visual review passed" to require receipts, so that agent text cannot silently become verified action state.
4. As Cursor acting as a challenger, I want to receive the same planning artifacts, Claude outcome, and evidence receipts as Codex, so that review is grounded in repo state rather than vibes.

## PRD Promise Contracts

P1. Interaction messages are rich and typed
User-visible promise: `dual_agent_interaction_message` events expose claims, objections, questions, tool receipts, evidence refs, raw transcript refs, persona id, addressed message ids, and "would change if" criteria.
Representative prompt/action: Run a workflow with Cursor review enabled and call `read_gate_transcript`.
Public boundary: `codex_supervisor_mcp`.
Allowed outcomes: transcript returns interaction payloads with those fields redacted and ordered.
Forbidden outcomes: transcript only shows opaque `content` and numeric confidence; Cursor or Claude claims are buried in metadata.
Related user stories: 1, 4.

P2. Claim verification is receipt-backed
User-visible promise: Built-in claims require matching evidence receipts and cannot pass merely because the worker says `test_status=passed` or the caller supplies `verified_claims`.
Representative prompt/action: Run outcome review where Claude claims "tests passed" and "implemented" without receipts.
Public boundary: `run_dual_agent_workflow`.
Allowed outcomes: workflow blocks with check ids for missing receipts.
Forbidden outcomes: workflow accepts because outcome fields look plausible.
Related user stories: 3.

P3. Resume prompt is state-rich
User-visible promise: `read_dual_agent_workflow_resume_prompt` returns current state, steps, latest event id, blockers, artifact output dir, transcript command, and next safe action.
Representative prompt/action: Block a workflow and ask for the resume prompt in a fresh chat.
Public boundary: `codex_supervisor_mcp`.
Allowed outcomes: prompt gives enough state to continue or inspect without relying on old chat memory.
Forbidden outcomes: prompt only includes run id, task id, current gate, and a vague instruction.
Related user stories: 2.

P4. Exports preserve evidence
User-visible promise: `interactions.md` and `transcript.md` render interaction claims, confidence rationale, evidence refs, tool receipts, and raw transcript refs.
Representative prompt/action: Export a run with interaction messages and inspect the generated Markdown.
Public boundary: `export_gate_artifacts`.
Allowed outcomes: Markdown shows receipt and evidence sections beside each message.
Forbidden outcomes: evidence is only present in raw SQLite JSON.
Related user stories: 1.

## Implementation Decisions

- Keep checks deterministic and LLM-free.
- Treat Cursor as a reviewer/challenger, not a writer.
- Keep the supervisor event ledger as the truth boundary.
- Keep `verified_claims` as deprecated compatibility input, but do not let it satisfy built-in receipt-gated claims by itself.
- Use generic receipt dictionaries now; defer a dedicated SQL receipt table until the event-envelope work.

## Testing Decisions

- First RED tests target MCP workflow and artifact export boundaries.
- Tests use fake Claude and fake Cursor runners; no live model, Telegram, Browser, or Computer Use calls.
- Claim receipt tests cover tests, implementation diff, push, and visual evidence.
- Resume prompt tests assert structured fields and human-readable prompt text.

## Out Of Scope

- Live Cursor SDK probe.
- Cryptographic proof that Browser or Computer Use produced a screenshot.
- A new SQL receipt table.
- LangGraph, Temporal, or other external orchestration frameworks.
