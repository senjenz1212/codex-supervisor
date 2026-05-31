# PRD To TDD Translation Audit: Dynamic Workflow Fan-out And Recovery

| Promise | First RED Boundary | Test Proof | Forbidden Outcome Covered |
|---|---|---|---|
| P6 Replay-verified dynamic receipts | `codex_supervisor_mcp` | forged refs block before Claude | fake non-empty refs pass P13 |
| P7 Reviewer registry and N-agent synthesis | `codex_supervisor_mcp` | manifest and critical disagreement tests | unregistered or blocking reviewer ignored |
| P8 Automatic dynamic receipts | `codex_supervisor_mcp` | subagent result receipts synthesize P13 receipts | operator must duplicate every preview-gate receipt |
| P9 Stale lock reclaim | `dual_agent_runner` | dead/stale lock reclaimed, live lock blocks | stale lock requires manual cleanup |
| P10 Transport resume | `codex_supervisor_mcp` | rerun skips accepted gates and continues pending gate | transport recovery repeats all gates |
| P11 Optional SDK import health | `agent_invoker` | import succeeds without SDK | pytest collection fails on optional dependency |
| P12 Durable MCP workflow job recovery | `codex_supervisor_mcp` | submit returns durable job id; poll reads completed result | long workflow requires one live MCP call |

All tests must avoid live Claude, Cursor, Telegram, Codex Desktop, or network calls.
