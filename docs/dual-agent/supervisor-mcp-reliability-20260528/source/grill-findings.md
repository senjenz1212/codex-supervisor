# Supervisor MCP Reliability Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Does the fallback weaken the supervisor protocol by bypassing MCP?
resolution: The fallback calls the same `CodexSupervisorMcpAPI` boundary as the
MCP tool, uses the same config and state database, and writes the same workflow
artifacts. It changes only the transport, not the review semantics.

### Finding G2

status: resolved
severity: high
question: Can a direct MCP server fix prove Codex Desktop MCP is fixed?
resolution: No. The fix proves the server stdio boundary is clean under direct
JSON-RPC. The how-to explicitly says a same-session Desktop client can still be
wedged and should be restarted before another MCP retry is trusted.

### Finding G3

status: resolved
severity: medium
question: Could Cursor review become noisy by reviewing every workflow gate?
resolution: Cursor gate selection is now explicit policy. Default review covers
the outcome gate, rigorous review covers quality gates, and vague work forces
planning gates. The workflow route records the selected gates for audit.

### Finding G4

status: waived
severity: low
question: Should the CLI fallback parse full TOML with a dependency instead of
a narrow MCP env block parser?
reason: The project does not need a new dependency for this narrow config
surface. The parser intentionally reads only the exact env block and has a
regression test for non-overriding behavior.

## Decision

No open findings remain. The implementation can proceed with local test
receipts and a supervisor rigorous review using the fallback transport if MCP
is unavailable in the current Desktop session.
