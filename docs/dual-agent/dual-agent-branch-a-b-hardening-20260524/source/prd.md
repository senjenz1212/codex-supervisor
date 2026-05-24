# Dual-Agent Branch A+B Hardening PRD

## Problem Statement

The dual-agent workflow has live Claude `/lead` proof, fixture-backed Cursor review, and receipt-backed claim verification, but the remaining reliability gaps make the system too dependent on operator memory. The supervisor needs to record why failures happened, prove the PRD-to-TDD skill chain ran, expose Codex's review criteria, and give live Cursor/UI probes a repeatable evidence path without pretending an unavailable credential or UI run succeeded.

## Solution

Add deterministic harness hardening in the supervisor layer. Every dual-agent event should carry a trace envelope with policy verdict and failure taxonomy. `run_dual_agent_workflow` should require PRD/TDD skill receipts by default before starting the gate sequence. Codex's review should be exported as a structured packet with requirements, evidence refs, confidence criteria, and would-change-if criteria. Cursor live probing should have a script that records either a green live result or a skipped diagnostic when `CURSOR_API_KEY` is absent.

## User Stories

1. As Sam, I want blocked gates to say what kind of failure happened so I can review postmortems without rereading the entire chat.
2. As Sam, I want proof that Codex ran PRD/TDD skills, not just proof that PRD/TDD-looking files exist.
3. As a future fresh chat, I want a single event envelope shape so workflow state can be resumed and rendered safely.
4. As Codex, I want my own review criteria recorded so a `0.95` confidence value is not an unexplained scalar.
5. As the tri-agent workflow, I want Cursor live probes to be honest: green only with a real SDK run, diagnostic otherwise.

## PRD Promise Contracts

P1. Failure taxonomy is deterministic
User-visible promise: Any blocked dual-agent event exposes a category, subcategory, code, and source.
Representative prompts or actions: Inspect a blocked workflow event or exported transcript.
Public boundary: supervisor event ledger and transcript export.
Allowed outcomes: MAST-inspired category is present for blocking probes and skill/claim/visual failures.
Forbidden outcomes: blocked event only says `blocked` or `agents_not_converged` with no taxonomy.
Related user stories: 1, 3

P2. Trace envelope is universal for dual-agent events
User-visible promise: Dual-agent event payloads retain their existing shape and include a `trace_envelope`.
Representative prompts or actions: Read `events.payload_json`, `read_gate_transcript`, or exported Markdown.
Public boundary: `State.write_event` for dual-agent events.
Allowed outcomes: top-level event fields remain backward compatible and trace metadata is appended.
Forbidden outcomes: event schema migration breaks older transcript readers.
Related user stories: 3

P3. PRD/TDD skill receipts gate workflow start
User-visible promise: `run_dual_agent_workflow` blocks before `prd_review` unless the PRD-to-TDD skill chain has passing receipts.
Representative prompts or actions: Start workflow with no `skill_run` receipts.
Public boundary: `run_dual_agent_workflow`.
Allowed outcomes: `P12 missing_prd_tdd_skill_receipts` blocks at `workflow_start`; valid receipts advance.
Forbidden outcomes: auto-seeded artifacts or prose claims bypass the missing skill receipts.
Related user stories: 2

P4. Codex reviewer packet replaces unexplained confidence scalars
User-visible promise: Codex decisions include criteria, evidence, requirements, objections, and would-change-if conditions.
Representative prompts or actions: Inspect `interactions.md` after a workflow run.
Public boundary: `dual_agent_interaction_message` for Codex gate decisions.
Allowed outcomes: review packet is exported and the gate-round confidence comes from the same `ConfidenceReport`.
Forbidden outcomes: hardcoded `0.95` / `0.7` callsite values are the only explanation.
Related user stories: 4

P5. Cursor/UI live probes remain honest
User-visible promise: live Cursor probe fixtures record either a real SDK outcome or a skipped diagnostic, and UI visual evidence remains Browser/Computer Use sourced.
Representative prompts or actions: Run `scripts/probe_cursor_sdk_live.py`; run a user-facing workflow without screenshots.
Public boundary: probe script and visual evidence gate.
Allowed outcomes: no-key Cursor probe records `missing_cursor_api_key`; UI gates block without visual receipts.
Forbidden outcomes: fixture-only Cursor or code-only UI validation is reported as live proof.
Related user stories: 5

## Implementation Decisions

- Keep taxonomy LLM-free and deterministic.
- Stamp a non-breaking `trace_envelope` at the event write boundary.
- Add `P12` as the PRD/TDD skill receipt probe.
- Use `tool_receipts` for skill receipts so the workflow has one receipt lane.
- Keep Cursor live credential handling outside committed files and shell history.

## Testing Decisions

- Add unit tests for taxonomy and trace envelope stamping.
- Extend workflow tests to prove missing skill receipts block and valid skill receipts advance.
- Extend transcript tests to include skill receipt validation.
- Keep visual evidence tests at the existing Browser/Computer Use receipt boundary.

## Out of Scope

Telegram workflow initiation, cryptographic screenshot receipts, a live Codex Desktop GUI run, and a green live Cursor SDK run without `CURSOR_API_KEY` in the environment are out of scope for this slice.
