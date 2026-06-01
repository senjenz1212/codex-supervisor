# Independent Reviewer Cursor Primary Issues

## Slice ISS-1: Default Runtime And Probe Truthfulness

Type: AFK
Priority: P0
Estimate: S
Scope: default reviewer output mode, workflow configuration preservation, and
live probe credential/runtime selection.
PRD promises: P1, P2, P7
Public-boundary RED test: workflow defaults construct reviewer requests with
`reviewer_output_mode="cursor_sdk"`, and the live probe script records
`missing_cursor_api_key` rather than `missing OPENAI_API_KEY` when no Cursor key
is present.

Acceptance Criteria:

- [ ] Supervisor config default reviewer output mode is `cursor_sdk`.
- [ ] Explicit `litellm_structured` remains configurable.
- [ ] Probe script loads Codex MCP env before deciding whether the Cursor key is
  absent.
- [ ] Probe script always forces `cursor_sdk`.
- [ ] Missing-key probe skip is honest and mentions Cursor, not OpenAI.

## Slice ISS-2: Cursor SDK Timeout And Diagnostics

Type: AFK
Priority: P0
Estimate: M
Scope: Cursor SDK invocation metadata, timeout classification, and
infrastructure failure diagnostics.
PRD promises: P3, P7
Public-boundary RED test: a fake Cursor SDK runner that blocks beyond
`timeout_s` returns a recoverable infrastructure failure with prompt-size and
timeout diagnostics.

Acceptance Criteria:

- [ ] `timeout_s` bounds SDK text/wait execution.
- [ ] Every attempt records prompt chars, attempt number, status, model,
  duration, run id, agent id, retry reason, and error details when available.
- [ ] Empty transcript failures include raw metadata in result details.
- [ ] Timeout is classified as recoverable reviewer infrastructure, not as an
  accept or an uncaught exception.

## Slice ISS-3: Honest Gemini Fallback

Type: AFK
Priority: P0
Estimate: M
Scope: fallback invocation, assurance labeling, and preservation of review
safety semantics after Cursor failure.
PRD promises: P4, P6, P7
Public-boundary RED test: forced Cursor SDK failure with a successful structured
fallback returns a valid reviewer outcome whose runtime is
`litellm_structured`, assurance is lower/degraded, and fallback reason is
persisted.

Acceptance Criteria:

- [ ] Fallback runs only after Cursor SDK failure after retries/timeout.
- [ ] Fallback verdict records `reviewer_runtime="litellm_structured"`.
- [ ] Fallback payload records lower assurance and original Cursor failure.
- [ ] Fallback revise/deny still blocks.
- [ ] Both Cursor and fallback failure route to existing reviewer-unavailable
  recovery.

## Slice ISS-4: Independent Reviewer Boundary Rename

Type: AFK
Priority: P0
Estimate: M
Scope: workflow result payloads, transcript/export payloads, review packet
wording, and ADR documentation for the reviewer boundary migration.
PRD promises: P5, P6, P7
Public-boundary RED test: workflow result and transcript exports include
`independent_reviewer`, keep `cursor_review` as an alias, and no new payload
uses Cursor naming for a Gemini runtime.

Acceptance Criteria:

- [ ] `independent_reviewer` is present on workflow/gate payloads.
- [ ] `cursor_review` remains as a compatibility alias.
- [ ] Review packet requirements use independent reviewer wording where
  possible.
- [ ] Transcript/export payloads expose reviewer runtime and assurance.
- [ ] ADR records the boundary rename rationale and migration plan.

## Slice ISS-5: Deterministic Replay Coverage

Type: AFK
Priority: P0
Estimate: M
Scope: deterministic regression coverage and exported evidence for all runtime
branches.
PRD promises: P1-P7
Public-boundary RED test: focused pytest suite covers default Cursor, explicit
Gemini, probe skip, diagnostics, fallback success, fallback rejection,
both-fail recovery, and alias payloads without live credentials.

Acceptance Criteria:

- [ ] Focused tests pass without live credentials.
- [ ] Full suite passes.
- [ ] Live phase0 artifacts are linked from planning docs.
- [ ] Replay artifacts include reviewer runtime/assurance fields.

## Coverage Map

| Promise | Covering slices |
|---|---|
| P1 | ISS-1, ISS-5 |
| P2 | ISS-1, ISS-5 |
| P3 | ISS-2, ISS-5 |
| P4 | ISS-3, ISS-5 |
| P5 | ISS-4, ISS-5 |
| P6 | ISS-3, ISS-4, ISS-5 |
| P7 | ISS-1, ISS-2, ISS-3, ISS-4, ISS-5 |
