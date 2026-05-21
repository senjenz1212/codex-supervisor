# Issue 06: Spike Codex App-Server as Future Target Surface

## What to build

Investigate whether Codex app-server can replace rollout tailing and hooks as a
richer event/approval source without changing Telegram conversation behavior.

## PRD Promise

**Promise IDs:** CS6

**Public boundary:** `target_adapter_conformance`

**Allowed outcomes:** app-server support is represented as an adapter
capability; rollout/hook monitoring remains default.

**Forbidden outcomes:** Telegram conversation code imports app-server payloads;
Codex Desktop monitoring waits for app-server.

## TDD Plan

RED: Add adapter conformance fixtures for app-server-shaped events only if the
spike proves the API is stable enough.

GREEN: Deferred.

## Status

Deferred.
