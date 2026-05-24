# Implementation Plan: Visual Validation Evidence Gate

## Files To Touch

- `mcp_tools/codex_supervisor_stdio.py`
- `supervisor/dual_agent_artifacts.py`
- `skills/dual-agent-gate.md`
- `docs/how-to/dual-agent-from-new-chat.md`
- `docs/testing/dual-agent-slice0-coverage-index.md`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `tests/test_dual_agent_artifacts.py`
- `tests/test_dual_agent_desktop_scope_docs.py`

## Plan

1. Add accepted visual evidence constants and helper functions to the Codex supervisor MCP boundary.
2. Validate screenshot image headers before accepting screenshot paths.
3. Add `_visual_validation_evidence` to enforce allowed source and passed validation status.
4. Include `visual_validation` in artifact preflight payloads and block strict user-facing gates when it is not ok.
5. Extend `ScreenshotArtifact` and `screenshots.md` rendering with source, validation status, and validation notes.
6. Update operator docs and coverage index.
7. Add regression tests for blocked and accepted user-facing paths.

## Validation

Run focused tests, full tests, compileall, and diff whitespace checks.

## Risks

- The source/provenance metadata is self-reported by Codex. This improves enforcement but is not signed proof.
- Header sniffing is deliberately simple and only recognizes common web screenshot formats.
