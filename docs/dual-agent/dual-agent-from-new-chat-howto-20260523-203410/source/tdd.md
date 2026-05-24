# TDD Plan: Dual-Agent From A New Chat How-To

## Public Boundary

The public boundary is the checked-in operator documentation at `docs/how-to/dual-agent-from-new-chat.md`.

## Tests

- The doc mentions the MCP registration command and config path.
- The doc names `read_gate_transcript`, `read_outcome`, and `interactions.md`.
- The doc names all strict gate sequence steps.
- The doc includes `artifact_policy="strict"`, `planning_artifacts`, and immutable planning artifacts.
- The doc includes `gate_prerequisites_missing`, `required_artifacts_missing`, `user_facing=True`, and `poll_resume_signal`.

## Verification

- `uv run pytest tests/test_dual_agent_desktop_scope_docs.py -q`
- `uv run pytest -q`
- Live Claude outcome review.
