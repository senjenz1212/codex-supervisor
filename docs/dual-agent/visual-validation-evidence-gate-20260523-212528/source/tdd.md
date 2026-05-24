# TDD Plan: Visual Validation Evidence Gate

## Public Boundary

The public boundary is the Codex-facing MCP tool `start_dual_agent_gate` and the artifact exporter `export_dual_agent_run_artifacts`.

## Tests

- `test_codex_supervisor_mcp_blocks_user_facing_gate_without_screenshots`
  - proves no-screenshot user-facing gates block before Claude Code.
- `test_codex_supervisor_mcp_blocks_user_facing_gate_without_visual_validation`
  - proves screenshot-only evidence blocks before Claude Code and reports `visual_validation`.
- `test_codex_supervisor_mcp_accepts_user_facing_gate_with_screenshots`
  - proves valid image, `computer_use` source, and passed validation status allow the gate to proceed.
- `test_export_dual_agent_run_artifacts_copies_screenshots_and_writes_manifest`
  - proves artifact export copies screenshot files and renders source/status/notes.
- `test_dual_agent_skill_uses_desktop_chat_when_telegram_is_absent`
  - proves skill docs require the evidence contract.
- `test_new_chat_how_to_covers_dual_agent_handoff_flow`
  - proves handoff docs include the payload shape.

## Verification Commands

- `uv run pytest tests/test_codex_supervisor_mcp_stdio.py tests/test_dual_agent_artifacts.py tests/test_dual_agent_desktop_scope_docs.py -q`
- `uv run pytest -q`
- `uv run python -m compileall -q supervisor mcp_tools tests`
- `git diff --check`

## Expected Failure Modes

- Screenshot is missing or not an image: block with `screenshots`.
- Screenshot exists but lacks source or passed validation: block with `visual_validation`.
- Non-user-facing gate: no visual validation requirement.
