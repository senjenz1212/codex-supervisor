# Supervisor MCP Reliability Issues

## Slice ISS-1: Clean MCP Stdio Transport

Type: AFK
Priority: P0
Estimate: S
Scope: Harden the Codex supervisor MCP server so routine MCP request logging
does not contaminate stdio protocol output during real JSON-RPC calls.
PRD promise: P1
First public-boundary RED test: `test_codex_supervisor_mcp_stdio_tools_call_keeps_protocol_stream_clean`

Acceptance Criteria:
- [ ] A subprocess MCP `tools/call` returns valid JSON-RPC response lines.
- [ ] Routine `Processing request` logging is absent from stdout and stderr.
- [ ] Existing MCP tool registration tests continue to pass.

## Slice ISS-2: Non-MCP Workflow Fallback

Type: AFK
Priority: P0
Estimate: M
Scope: Add a CLI command that reads a JSON workflow request, loads supervisor
configuration and secrets, invokes the same workflow API as MCP, and writes an
optional JSON result file for durable inspection.
PRD promise: P2, P4
First public-boundary RED test: `test_workflow_cli_payload_runs_same_supervisor_api`

Acceptance Criteria:
- [ ] The fallback calls `CodexSupervisorMcpAPI.run_dual_agent_workflow`.
- [ ] The fallback can load Cursor and model keys from Codex MCP env without
      overriding explicit shell values.
- [ ] A smoke run returns a real supervisor verdict rather than transport text.
- [ ] Documentation explains when to use the fallback.

## Slice ISS-3: Cursor Rigorous Gate Policy

Type: AFK
Priority: P1
Estimate: M
Scope: Make Cursor review gate selection an explicit workflow policy with
default, rigorous, vague, and override modes.
PRD promise: P3
First public-boundary RED test: `test_cursor_review_gate_profiles_are_policy_not_prompt`

Acceptance Criteria:
- [ ] Default Cursor review runs at the outcome gate.
- [ ] Rigorous Cursor review runs at TDD, implementation plan, and outcome.
- [ ] Vague work forces planning-focused Cursor gates even when the caller does
      not explicitly request review.
- [ ] Cursor rejection blocks the selected gate and is reflected in transcript
      artifacts.

## Slice ISS-4: Operator Recovery Documentation

Type: AFK
Priority: P2
Estimate: S
Scope: Update the new-chat dual-agent how-to with primary MCP, fallback CLI, and
artifact-only recovery modes.
PRD promise: P4
First public-boundary RED test: documentation review plus `git diff --check`

Acceptance Criteria:
- [ ] The how-to says MCP `Transport closed` is not a supervisor verdict.
- [ ] The how-to includes a runnable fallback command shape.
- [ ] The how-to preserves artifact-only review as a weaker last resort.
