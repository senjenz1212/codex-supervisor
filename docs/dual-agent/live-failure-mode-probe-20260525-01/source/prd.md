# Live Failure Mode Probe PRD

## Problem Statement

The dual-agent harness has a happy-path live proof, but operators also need a
live proof that model agreement cannot bypass receipt governance. A Claude
Code outcome can look confident, Cursor can review it, and the supervisor must
still block when implementation and test claims lack durable receipts.

## Solution

Run a small outcome-review probe in a disposable repository. Claude Code `/lead`
returns a valid accepted outcome that claims tests passed and implementation
finished. Cursor performs an optional read-only tri-agent review. Codex then
uses deterministic supervisor claim verification without any test or diff
receipts, so the final status must be blocked with a P11 receipt failure.

## User Stories

1. As an operator, I want a live blocked probe, so that the reliability layer is
   proven on failure paths instead of only on successful runs.
2. As a reviewer, I want the model transcript preserved, so that I can inspect
   what Claude and Cursor claimed before the supervisor blocked.
3. As a future fresh chat, I want the failure taxonomy and trace envelope, so
   that I can resume from evidence rather than chat memory.

## PRD Promise Contracts

P1. Live Claude claim fixture is captured
User-visible promise: A real Claude Code `/lead` call returns a typed outcome
with implementation and test claims.
Representative prompts or actions: Run `scripts/probe_live_failure_mode.py`.
Public boundary: `supervisor.dual_agent_runner.run_dual_agent_gate`
Allowed outcomes: accepted Claude gate with P1/P2/P3/P_planning green.
Forbidden outcomes: fabricated stdout or missing dual_agent_outcome transcript.
Related user stories: 1, 2

P2. Tri-agent review remains read-only
User-visible promise: Cursor may review the Claude outcome without modifying
the disposable worktree.
Representative prompts or actions: Run the probe with `CURSOR_API_KEY` present.
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Allowed outcomes: Cursor returns a valid typed review, or the probe records a
skipped diagnostic when no key is present.
Forbidden outcomes: Cursor edits files or the key appears in artifacts.
Related user stories: 2

P3. Supervisor blocks unreceipted claims
User-visible promise: Codex/supervisor denies completion when tests passed or
implemented claims have no matching receipt.
Representative prompts or actions: Inspect `summary.json` and transcript.
Public boundary: `supervisor.dual_agent_workflow.verify_workflow_claims`
Allowed outcomes: P11 red with tests_passed_without_test_receipt and
implemented_without_diff_receipt.
Forbidden outcomes: accepted final status with no test or git-diff receipts.
Related user stories: 1, 3

## Implementation Decisions

- Use a disposable git repository for live model calls.
- Persist the live Claude stdout as a replay fixture.
- Persist Cursor transcript only after redaction.
- Store the final blocked decision as a supervisor ledger event.

## Testing Decisions

Tests replay the captured Claude stdout through `invoke_claude_lead`, parse the
Cursor transcript when available, and assert the recorded P11 failure taxonomy
is `task_verification/missing_or_stale_receipt`.

## Out of Scope

This probe does not validate Browser screenshots, Telegram control commands, or
cryptographically signed tool receipts. Those surfaces require separate live
tool-side evidence.
