# Issues: Visual Validation Evidence Gate

## Issue 1: Enforce Visual Evidence In Gate Preflight

Add a supervisor preflight check that blocks strict `user_facing=True` gates when screenshot evidence is missing, not a valid image, missing Browser/Computer Use provenance, or missing a passed visual validation status.

## Issue 2: Persist Visual Evidence In Artifacts

Extend screenshot artifact rendering so `screenshots.md` includes source, validation status, and validation notes next to the copied image.

## Issue 3: Update Operator Instructions

Update `skills/dual-agent-gate.md`, the new-chat handoff guide, and the Slice 0 coverage index so Codex operators know the exact visual evidence payload required.

## Issue 4: Add Regression Tests

Add focused tests for blocking screenshot-only user-facing gates, accepting valid evidence, and rendering visual validation metadata.
