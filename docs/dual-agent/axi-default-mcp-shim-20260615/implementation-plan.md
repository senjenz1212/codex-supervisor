# AXI Default MCP Shim Implementation Plan

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_axi.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `tests/test_codex_supervisor_axi.py`
- `tests/test_dual_agent_workflow_driver.py`
- `docs/supervisor-axi-detached-dispatcher.md`
- `docs/LOOP.md`
- `docs/how-to/dual-agent-from-new-chat.md`
- `docs/testing/public-boundaries.md`
- `skills/dual-agent-gate.md`

## Steps

1. Add AXI/MCP cross-surface tests for same-token reattach in both directions
   and event-tail catch-up equivalence.
2. Add or strengthen MCP compatibility tests so `run_dual_agent_workflow`
   returns AXI JSON recovery hints while preserving
   `execution_model="detached_dispatcher_only"`.
3. Update AXI help text after submit and poll to show `codex-supervisor-axi
   --json` commands for automated recovery.
4. Update current default docs and skill guidance to make AXI/CLI the default
   orchestration path and MCP the compatibility/native-tool adapter.
5. Run focused AXI/MCP tests first, then the broader workflow driver and MCP
   stdio suites that own these public boundaries.

## Risks

- Documentation drift can be overcorrected by editing archived transcripts or
  historical analyses. The implementation should target current default
  instructions and preserve past evidence artifacts.
- JSON automation defaults can be confused with removing TOON-lite. The slice
  should leave existing human-readable output behavior and metrics intact.
- MCP compatibility can accidentally be weakened while improving help text. The
  tests must keep MCP tool names present and verify durable job reservation.
- Cross-surface tests can become brittle if they compare event payload fields
  affected by observational metrics. They should compare stable event IDs,
  kinds, counts, cursors, and job rows.

## Traceability

- P1 -> test_axi_submit_and_poll_help_use_json_recovery_commands
- P1 -> test_rigorous_flow_docs_use_axi_json_default_and_keep_mcp_compatibility
- P2 -> test_mcp_compatibility_run_workflow_help_prefers_axi_json_recovery
- P3 -> test_axi_then_mcp_same_client_token_reattaches_to_one_job
- P3 -> test_mcp_then_axi_same_client_token_reattaches_to_one_job
- P3 -> test_axi_and_mcp_catch_up_return_equivalent_event_tail
- P4 -> test_axi_submit_and_poll_help_use_json_recovery_commands
- P4 -> test_rigorous_flow_docs_use_axi_json_default_and_keep_mcp_compatibility
