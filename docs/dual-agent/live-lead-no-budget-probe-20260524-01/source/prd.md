# Live Lead Probe PRD

## Problem Statement

The supervisor can pass deterministic tests, but the operator still needs a live proof that Codex can invoke Claude Code through the portable `/lead` skill and preserve the resulting dialogue, receipts, and confidence fields. The live probe must be tiny, repo-safe, and inspectable without relying on chat memory.

## Solution

Use a disposable sandbox repository. Claude Code should add a small pytest regression test for a `slugify_label` helper, run the test to observe the intended failing behavior, implement the helper, rerun the test, and report the final result through `<dual_agent_outcome>`. Codex collects real pytest, git diff, and git status receipts after the execution gate.

## User Stories

1. As Sam, I want a live `/lead` execution on a disposable repo so I can distinguish real process proof from fixture replay.
2. As Codex, I want the transcript and stdout captures so I can replay or debug the live boundary later.
3. As the supervisor, I want claim verification to require pytest and git receipts rather than trusting the worker summary.

## PRD Promise Contracts

P1. Live Claude Code worker spawn
User-visible promise: `start_dual_agent_gate` invokes live Claude Code `/lead` from a sandbox worktree.
Representative prompts or actions: Run the execution gate with quality best and a high budget value.
Public boundary: codex_supervisor_mcp / start_dual_agent_gate.
Allowed outcomes: accepted or blocked with captured stdout/stderr and ledger events.
Forbidden outcomes: synthetic replay runner is used while claiming live proof.
Related user stories: 1, 2

P2. Sandbox implementation is test-backed
User-visible promise: The sandbox contains a focused pytest test and implementation for `slugify_label` after execution.
Representative prompts or actions: Run `python -m pytest -q` in the sandbox.
Public boundary: sandbox pytest command.
Allowed outcomes: tests pass and changed files are visible in git status.
Forbidden outcomes: worker claims implementation without a test or changed files.
Related user stories: 1, 3

P3. Receipts explain acceptance
User-visible promise: Claim verification only turns green when pytest, git diff, and git status receipts are supplied.
Representative prompts or actions: Call `verify_workflow_claims` on the live outcome with and without receipts.
Public boundary: supervisor.dual_agent_workflow.verify_workflow_claims.
Allowed outcomes: green with receipts; red for missing receipts when claims require them.
Forbidden outcomes: verified_claims or prose claims satisfy the gate alone.
Related user stories: 2, 3

## Implementation Decisions

- Use a temporary git repository outside the main repo.
- Use `quality=best` and `budget_usd=100.0` so the probe is not cost-constrained.
- Do not enable Cursor in probe 1.
- Do not use Codex subagents during the live probe.

## Testing Decisions

The live proof uses real Claude Code plus local deterministic receipts: captured Claude stdout/stderr, pytest output, git status output, and supervisor SQLite events.

## Out of Scope

Cursor SDK live review, Telegram control, Browser screenshots, and long high-volume model output are not part of this first live no-budget probe.
