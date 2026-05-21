# Issue 07: Preserve Codex Compatibility Behind the Adapter

## What to build

Move existing Codex-specific rollout watching, hook normalization, and resume
command behavior into `CodexAdapter` or compatibility modules. Add version-probed
resume behavior for current `codex exec resume <session_id> <prompt>` and older
known forms. Do not make Codex the default target.

## PRD promise

**Promise IDs:** P2

**User-visible promise:** Codex remains a future target that can be exercised
through the same adapter boundary without changing supervisor core logic.

**Public boundary:** `target_adapter_conformance`

**Allowed outcomes:** Codex adapter passes shared conformance tests with fake
rollout and fake CLI fixtures; unsupported app-server features report
`not_supported`.

**Forbidden outcomes:** Codex-specific rollout parsing inside drift detection;
hardcoded `codex exec` calls outside the adapter; a second supervisor loop for
Codex.

**Representative prompt/action:** Run conformance tests against fake Codex
rollout JSONL and fake CLI version output.

## Acceptance criteria

- [ ] Codex resume command construction is adapter-owned and version-probed.
- [ ] Existing rollout watcher behavior is either adapter-owned or feeds the
      adapter-owned normalized event stream.
- [ ] Codex hook payloads normalize to the same hook event shape.
- [ ] Core drift, replay, and action code have no Codex-only branches.
- [ ] App-server is documented as deferred, not partially mixed into v1.

## TDD plan

First public behavior: Codex adapter conformance test normalizes a fake rollout
event and builds the current resume command form without touching core drift
logic.

RED: Add a `target_adapter_conformance` test with fake `codex --version` and
`codex exec resume --help` outputs. Assert the adapter constructs the expected
resume command and returns normalized events.

GREEN: Move Codex-specific command building into `CodexAdapter`.

Next cycles:

- RED/GREEN for old resume form fallback.
- RED/GREEN for unsupported app-server feature returning `not_supported`.
- RED/GREEN for no Codex imports from drift detector.

## Blocked by

- `.scratch/agent-supervisor/01-target-adapter-foundation.md`
