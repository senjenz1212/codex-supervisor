# Codex MCP Support Probe

Date: 2026-05-23

## Result

Codex CLI can load and invoke a repo-provided stdio MCP server from
`codex exec`.

## Evidence

CLI version:

```text
codex-cli 0.133.0
```

Probe server was injected with command-line config overrides:

```bash
codex mcp list \
  -c 'mcp_servers.codex_probe.command="uv"' \
  -c 'mcp_servers.codex_probe.args=["run","python",".scratch/codex_mcp_probe_server.py"]' \
  -c 'mcp_servers.codex_probe.cwd="/Users/sam.zhang/Documents/codex-supervisor"'
```

`codex exec --json` then emitted:

```json
{"type":"item.started","item":{"type":"mcp_tool_call","server":"codex_probe","tool":"supervisor_probe_echo","arguments":{"text":"CODEX_MCP_PROBE_OK"}}}
{"type":"item.completed","item":{"type":"mcp_tool_call","server":"codex_probe","tool":"supervisor_probe_echo","result":{"content":[{"type":"text","text":"SUPERVISOR_PROBE_ECHO:CODEX_MCP_PROBE_OK"}],"structured_content":{"result":"SUPERVISOR_PROBE_ECHO:CODEX_MCP_PROBE_OK"}}}}
```

The probe server also wrote `.scratch/codex_mcp_probe_marker.txt` with:

```text
CODEX_MCP_PROBE_OK
```

## Decision

Codex-facing supervisor tools should be implemented as a stdio MCP server,
not as Claude Agent SDK in-process MCP wrappers.

The production entrypoint is:

```bash
uv run codex-supervisor-mcp --config ~/.codex-supervisor/config.yaml
```

To register it with Codex:

```bash
codex mcp add codex_supervisor -- \
  uv run codex-supervisor-mcp --config ~/.codex-supervisor/config.yaml
```

## Production Entrypoint Smoke

After adding `codex-supervisor-mcp`, `codex exec --json` successfully loaded
the production server and called:

```json
{"server":"codex_supervisor","tool":"start_codex_session","arguments":{"prompt":"probe","cwd":"/Users/sam.zhang/Documents/codex-supervisor","execute":false}}
```

The tool returned:

```json
{"status":"dry_run","argv":["codex","exec","--json","-C","/Users/sam.zhang/Documents/codex-supervisor","-m","gpt-5.5","-c","reasoning_effort=\"xhigh\"","[PROMPT_REDACTED]"]}
```

This validates Codex can consume the real supervisor MCP entrypoint. The smoke
kept `execute=false`, so it did not spawn a nested Codex session.
