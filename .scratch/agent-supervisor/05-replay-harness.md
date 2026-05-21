# Issue 05: Add Deterministic Replay Harness Before Enforcement

## What to build

Add a replay entrypoint that can load a fixture run, frozen config snapshot,
scope contract, normalized event stream, and model-output fixtures to reproduce
drift and action decisions without live services.

## PRD promise

**Promise IDs:** P7

**User-visible promise:** A developer can reproduce supervisor decisions from
saved inputs without live Claude Code, Codex, Telegram, or model APIs.

**Public boundary:** `replay_cli`

**Allowed outcomes:** deterministic normalized events, scope findings, verdicts,
and proposed actions; fixture model outputs are used by default.

**Forbidden outcomes:** replay calls live target agents, live Telegram, or live
LLM APIs by default; replay reads current config instead of snapshot.

**Representative prompt/action:** Run the replay entrypoint on a fixture with
out-of-scope writes and compare output with expected JSON.

## Acceptance criteria

- [ ] Add a replay module or CLI entrypoint.
- [ ] Replay accepts fixture paths for snapshot, events, and model outputs.
- [ ] Replay output includes normalized event count, scope findings, verdicts,
      and proposed actions.
- [ ] Replay fails if it would need a live external service unless explicitly
      configured.
- [ ] A sample fixture is included for a Claude Code drift scenario.

## TDD plan

First public behavior: replaying a fixture produces the expected drift verdict
without external service calls.

RED: Add a `replay_cli` test that patches network and subprocess calls to fail
if invoked, runs replay on fixtures, and compares output JSON.

GREEN: Implement replay loading and deterministic execution path.

Next cycles:

- RED/GREEN for missing model fixture fail-closed behavior.
- RED/GREEN for replay honoring frozen mode snapshot.
- RED/GREEN for event-ID citations in replay output.

## Blocked by

- `.scratch/agent-supervisor/02-state-snapshots-redaction.md`
- `.scratch/agent-supervisor/04-drift-cascade-normalized-events.md`
