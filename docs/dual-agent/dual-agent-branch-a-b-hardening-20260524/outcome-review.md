# Outcome Review: dual-agent-branch-a-b-hardening-20260524

## Summary

Accepted with caveats. Branch A is implemented end to end. Branch B is advanced where code can honestly prove the surface: Cursor has a live-probe harness and durable diagnostic fixture, UI visual evidence remains enforced at the Browser/Computer Use receipt boundary, Codex review packets are structured, and PRD/TDD skill receipts are now a workflow-start gate.

## Accepted Changes

- `State.write_event` stamps dual-agent payloads with a universal `trace_envelope`.
- Blocking failures now classify through a deterministic MAST-inspired taxonomy.
- `run_dual_agent_workflow` defaults to requiring PRD/TDD `skill_run` receipts.
- `read_gate_transcript` surfaces `dual_agent_skill_receipt_validation`.
- Codex gate decisions include a structured `codex-review-packet/v1`.
- Gate-round Codex confidence is propagated from `ConfidenceReport.value`.
- Cursor SDK live probing is available through `scripts/probe_cursor_sdk_live.py`.
- Health and coverage docs distinguish fixture coverage, diagnostic skipped probes, and true live proof.

## Validation

- `uv run pytest tests/test_failure_taxonomy.py tests/test_agent_mailbox.py tests/test_agent_interaction_snapshot.py tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py tests/test_cursor_agent.py -q` -> `102 passed`
- `uv run pytest -q` -> `396 passed`
- `python3 -m compileall -q supervisor mcp_tools scripts tests` -> passed
- Secret-pattern scan over new artifacts and code -> no matches

## Caveats

- Live Cursor SDK review is not green because `CURSOR_API_KEY` is not present in the current environment. The diagnostic fixture is at `docs/dual-agent/live-cursor-sdk-probe-20260524-01/summary.json`.
- No new live Codex Desktop GUI probe was run in this slice.
- No new real product UI screenshot was captured in this slice; the screenshot gate remains code- and fixture-validated.
- Existing unrelated untracked Vela routing artifacts remain untouched.

## Verdict

Accept this slice as supervisor hardening plus honest Branch B scaffolding. The next live step is to export `CURSOR_API_KEY` into the environment and rerun `scripts/probe_cursor_sdk_live.py`, then run a real user-facing UI workflow with Browser/Computer Use screenshots.
