# PRD: Visual Validation Evidence Gate

## Problem

User-facing dual-agent outcome-review gates previously required screenshot paths when `user_facing=True`, but the supervisor accepted any existing file. That allowed a gate to pass with a bare or fake screenshot and no evidence that Codex used Browser or Computer Use to validate the UI.

## Goal

Make visual validation a supervisor-enforced evidence contract for user-facing gates. A strict `user_facing=True` gate must block unless Codex supplies valid screenshot evidence with Browser/Computer Use provenance and an explicit passed visual review.

## Acceptance Criteria

- Strict user-facing gate with no screenshots blocks before Claude Code runs.
- Strict user-facing gate with a screenshot file but no Browser/Computer Use provenance blocks before Claude Code runs.
- Strict user-facing gate with a screenshot file but no passed visual validation status blocks before Claude Code runs.
- Strict user-facing gate with a valid image, accepted source, and passed visual validation can proceed.
- Artifact export preserves visual source, validation status, and validation notes in `screenshots.md`.
- Documentation and dual-agent skill instructions describe the required payload shape.
- Existing non-user-facing gates remain unaffected.

## Non-Goals

- Cryptographically proving that Browser or Computer Use produced the screenshot.
- Running a real GUI automation session inside the supervisor.
- Replacing Codex's responsibility to inspect the UI state.

## Confidence Targets

- Implementation correctness: >= 0.95
- Regression risk: low
- Remaining limitation is explicit: the supervisor enforces evidence/provenance contract, not signed tool attestation.
