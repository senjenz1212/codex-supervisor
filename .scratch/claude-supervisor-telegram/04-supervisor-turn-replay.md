# Issue 04: Replay Supervisor Telegram Turns

## What to build

Replay a Telegram supervisor turn from frozen message, tool outputs, model
output, and proposed actions without live Telegram, target agents, or models.

## PRD Promise

**Promise IDs:** CS5

**Public boundary:** `supervisor_turn_replay`

**Allowed outcomes:** replay output includes response text, tool calls, and
proposed actions from fixtures.

**Forbidden outcomes:** replay calls live Telegram, Codex, Claude, OpenAI, or
subprocesses; replay reads current config instead of frozen inputs.

## TDD Plan

RED: Patch httpx and subprocess to fail, replay a frozen Vela supervisor turn,
and assert output equals fixture values.

GREEN: Add `supervisor/supervisor_turn_replay.py`.

## Status

Implemented. Covered by `tests/test_supervisor_turn_replay.py`.
