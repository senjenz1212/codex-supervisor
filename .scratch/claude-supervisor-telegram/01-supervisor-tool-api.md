# Issue 01: Add Supervisor Tool API for Claude Conversation

## What to build

Expose a stable local tool API for Claude supervisor turns: list runs, resolve a
session, read recent events, read hooks, read actions, evaluate scope, and
propose mode-safe actions.

## PRD Promise

**Promise IDs:** CS3

**Public boundary:** `supervisor_tool_api`

**Allowed outcomes:** tools return redacted, bounded, normalized JSON with ids;
mutating intent becomes proposed actions only.

**Forbidden outcomes:** Claude receives raw SQLite rows, raw target payload
shapes, unredacted secrets, or direct SQL access.

## TDD Plan

RED: Call `SupervisorToolAPI` against fake state with event, hook, and action
rows containing secret-like strings. Assert returned JSON is redacted and
bounded.

GREEN: Add `supervisor/supervisor_tools.py` and MCP wrappers.

## Status

Implemented. Covered by `tests/test_supervisor_tool_api.py`.
