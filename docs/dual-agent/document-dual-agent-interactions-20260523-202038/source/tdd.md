# TDD Plan: Document Dual-Agent Interactions

## Public Boundary

The public boundary is `export_dual_agent_run_artifacts`.

## Tests

- Exported file list includes `interactions.md`.
- `interactions.md` includes round-level Codex and Claude Code decisions.
- `interactions.md` includes gate-result outcome summaries.
- Skill docs mention `interactions.md` and Codex/Claude dialogue.

## Verification

- Focused artifact/doc/MCP tests.
- Full pytest suite.
- Live Claude outcome review.
