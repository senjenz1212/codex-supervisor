# Issue 03: Make Hook Server Target-Aware for Claude Code Hooks

## What to build

Update the FastAPI hook server so Claude Code hook payloads can be accepted,
normalized, audited, and answered according to operating mode. Treat
`PermissionRequest` as a first-class event. Keep Codex hook support behind the
future adapter path.

## PRD promise

**Promise IDs:** P3, P4

**User-visible promise:** Claude Code hook events are accepted, normalized,
persisted, and answered without shadow mode taking external action.

**Public boundary:** `hook_http_api`, `mode_policy`

**Allowed outcomes:** event is stored with redacted raw payload; normalized event
is available; response shape is valid for Claude Code hooks; shadow logs only;
advise recommends only; enforce acts only for allowed capabilities.

**Forbidden outcomes:** raw hook payload is dropped; hook response blocks in
shadow mode; permission requests are unknown events; advise denies a hook action.

**Representative prompt/action:** POST Claude Code `PreToolUse` and
`PermissionRequest` sample payloads to `/hook/claude-code`.

## Acceptance criteria

- [ ] Add a Claude Code hook endpoint or route mapping that uses the selected
      target adapter.
- [ ] Store every hook call in `hook_requests`.
- [ ] Normalize `PreToolUse`, `PostToolUse`, `PermissionRequest`,
      `UserPromptSubmit`, `Stop`, `SessionStart`, and `SessionEnd` where payload
      samples are available.
- [ ] Shadow mode cannot deny or mutate hook behavior.
- [ ] Deterministic deny rules are separated from optional LLM critique.

## TDD plan

First public behavior: posting a Claude Code `PermissionRequest` hook in shadow
mode stores the hook request and returns a non-blocking response.

RED: Add a `hook_http_api` test using FastAPI's test client and a sample
`PermissionRequest` payload. Assert response is non-blocking and `hook_requests`
contains redacted raw payload plus normalized event metadata.

GREEN: Implement target-aware hook route, audit write, normalization, and shadow
mode response.

Next cycles:

- RED/GREEN for `PreToolUse` deterministic destructive-command deny in enforce
  mode.
- RED/GREEN for advise mode producing a Telegram recommendation without deny.
- RED/GREEN for malformed payload safe default with audit row.

## Blocked by

- `.scratch/agent-supervisor/01-target-adapter-foundation.md`
- `.scratch/agent-supervisor/02-state-snapshots-redaction.md`
