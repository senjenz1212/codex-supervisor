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

1. Add public-boundary tests for AXI-to-MCP reattach, MCP-to-AXI reattach, and
   AXI/MCP catch-up equivalence.
2. Add a compatibility test proving MCP `run_dual_agent_workflow` returns AXI
   JSON recovery hints and never calls inline phase execution.
3. Update AXI submit and poll help to prefer JSON recovery commands.
4. Update current default docs and skill guidance to make AXI/CLI the default
   orchestrator while retaining MCP as compatibility.
5. Run focused AXI and workflow-driver tests, then broaden to MCP stdio tests
   if touched behavior overlaps.

## Risks

- Editing historical transcripts would corrupt evidence instead of fixing live
  defaults. Only current docs and skill guidance should change.
- Help text can regress to non-JSON examples. Tests must parse emitted help.
- MCP compatibility can accidentally look like the execution owner. Tests must
  forbid inline runner calls and request-file side effects.
- Event-tail equivalence can be brittle if observational metrics are compared.
  Tests should compare stable returned event IDs, kinds, counts, and cursors;
  MCP catch-up must remain read-only, while any AXI metric must be
  observational-only and outside gate authority.

## Traceability

- P1 -> test_axi_submit_and_poll_help_use_json_recovery_commands
- P1 -> test_rigorous_flow_docs_use_axi_json_default_and_keep_mcp_compatibility
- P2 -> test_mcp_compatibility_run_workflow_help_prefers_axi_json_recovery
- P3 -> test_axi_then_mcp_same_client_token_reattaches_to_one_job
- P3 -> test_mcp_then_axi_same_client_token_reattaches_to_one_job
- P3 -> test_axi_and_mcp_catch_up_return_equivalent_event_tail
- P4 -> test_axi_submit_and_poll_help_use_json_recovery_commands
- P4 -> test_rigorous_flow_docs_use_axi_json_default_and_keep_mcp_compatibility
