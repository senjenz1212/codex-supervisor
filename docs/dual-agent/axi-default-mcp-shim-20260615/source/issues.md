# AXI Default MCP Shim Issues

## Slice 1 - Cross-surface durable retry tests

Scope: Add public-boundary tests proving AXI and MCP share client-token
idempotency and catch-up event tails.

PRD promise: P3
Public boundary: `codex_supervisor_axi`, `codex_supervisor_mcp`
Priority: P0

Acceptance criteria:
- [ ] AXI submit followed by MCP submit with the same client token returns the
  same job id and exactly one durable job row.
- [ ] MCP submit followed by AXI submit with the same client token returns the
  same job id and exactly one durable job row.
- [ ] AXI catch-up and MCP catch-up return equivalent event IDs, kinds, counts,
  and cursors for one run.
- [ ] MCP catch-up remains read-only; AXI catch-up may record only
  observational transport metrics after reading and those metrics cannot
  advance, block, or mutate gate authority.

## Slice 2 - MCP compatibility recovery hints and no inline execution

Scope: Strengthen MCP compatibility responses and tests so legacy callers see
AXI recovery commands while MCP remains a non-blocking shim.

PRD promise: P2
Public boundary: `codex_supervisor_mcp`
Priority: P0

Acceptance criteria:
- [ ] Compatibility `run_dual_agent_workflow` returns
  `execution_model="detached_dispatcher_only"`.
- [ ] Compatibility help includes AXI `--json poll` and `--json catch-up`.
- [ ] The compatibility path does not call the synchronous gate runner.
- [ ] MCP submit, poll, and catch-up do not spawn workers or write request
  files.

## Slice 3 - Default docs and skill prompt migration

Scope: Update current supervisor docs and skill guidance so rigorous workflows
default to AXI JSON and describe MCP as compatibility/native-tool access.

PRD promise: P1, P4
Public boundary: documentation contract
Priority: P1

Acceptance criteria:
- [ ] `docs/supervisor-axi-detached-dispatcher.md` documents AXI `--json`
  submit, poll, and catch-up as default.
- [ ] `docs/how-to/dual-agent-from-new-chat.md` starts and recovers workflows
  through AXI JSON by default.
- [ ] `skills/dual-agent-gate.md` prefers AXI for whole-workflow orchestration.
- [ ] Current docs avoid TOON performance claims.

## Slice 4 - AXI help text JSON automation default

Scope: Update AXI help strings to prefer JSON recovery commands while keeping
TOON-lite output as human-readable behavior.

PRD promise: P1, P4
Public boundary: `codex_supervisor_axi`
Priority: P1

Acceptance criteria:
- [ ] AXI submit help includes `codex-supervisor-axi --json poll <job_id>`.
- [ ] AXI poll help includes `codex-supervisor-axi --json catch-up <run_id>`.
- [ ] AXI home/help does not imply MCP-first operation.
- [ ] Existing TOON-lite tests remain green.
