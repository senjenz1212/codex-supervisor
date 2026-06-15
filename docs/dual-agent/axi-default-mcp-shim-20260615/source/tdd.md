# AXI Default MCP Shim TDD Plan

## test_axi_then_mcp_same_client_token_reattaches_to_one_job

Maps to: P3, Slice 1
Public boundary: `codex_supervisor_axi` and `codex_supervisor_mcp`

RED: Submit a job through AXI with a stable client token, then submit the same
logical workflow through MCP with that token. Assert the second response
reattaches to the first job and the durable job table has one row.

GREEN: Keep AXI and MCP on the shared `CodexSupervisorMcpAPI` idempotency path.

## test_mcp_then_axi_same_client_token_reattaches_to_one_job

Maps to: P3, Slice 1
Public boundary: `codex_supervisor_mcp` and `codex_supervisor_axi`

RED: Submit through MCP first, then submit through AXI with the same client
token. Assert the AXI response returns the MCP-created job id and no second job
is reserved.

GREEN: Pass `client_token` unchanged from AXI into the shared core API.

## test_axi_and_mcp_catch_up_return_equivalent_event_tail

Maps to: P3, Slice 1
Public boundary: `codex_supervisor_axi` and `codex_supervisor_mcp`

RED: Seed two events for one run, call MCP catch-up and AXI catch-up from the
same cursor, and compare returned event IDs, kinds, counts, and next cursor.
Assert MCP catch-up writes no new events. If AXI writes a transport or format
metric around catch-up, assert that event is explicitly observational
(`observational_only=true`, `gate_authority="unchanged"`) and is not included
in the returned caller event tail for the cursor being compared.

GREEN: Keep MCP catch-up as a read-only event-tail query and keep AXI metrics
outside gate authority.

## test_mcp_compatibility_run_workflow_help_prefers_axi_json_recovery

Maps to: P2, Slice 2
Public boundary: `codex_supervisor_mcp`

RED: Call public MCP `run_dual_agent_workflow` with a forbidden synchronous
runner installed. Assert it reserves a job, returns
`detached_dispatcher_only`, creates no request file, and includes AXI JSON
poll/catch-up commands in help.

GREEN: Update compatibility help without invoking the old inline runner or
dispatcher.

## test_axi_submit_and_poll_help_use_json_recovery_commands

Maps to: P1, P4, Slice 4
Public boundary: `codex_supervisor_axi`

RED: Run AXI `--json submit` and `--json poll`, parse help lines, and assert
they contain JSON-form poll and catch-up commands.

GREEN: Change AXI help text; leave human-readable output behavior intact.

## test_rigorous_flow_docs_use_axi_json_default_and_keep_mcp_compatibility

Maps to: P1, P2, P4, Slice 3
Public boundary: documentation contract

RED: Read the current AXI docs, new-chat how-to, and dual-agent skill. Assert
they include AXI JSON submit, poll, and catch-up defaults, retain MCP
compatibility language, and do not say TOON improves agent performance.

GREEN: Update current operating docs and skill guidance only.
