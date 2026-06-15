# AXI CLI And Detached Workflow Dispatcher

`codex-supervisor-axi` is the preferred operator surface for durable workflow
jobs. It uses the same submit, poll, and catch-up core as the MCP shim, but each
call is one short-lived process invocation and the ledger is the only shared
state.

Workflow phase execution belongs only to the detached dispatcher:

```bash
codex-supervisor-workflow-dispatcher --config ~/.codex-supervisor/config.yaml
```

The macOS launchd plist should live at:

```text
~/Library/LaunchAgents/com.codex-supervisor.workflow-dispatcher.plist
```

Minimal plist command shape:

```xml
<array>
  <string>/Users/sam.zhang/Documents/codex-supervisor/.venv/bin/codex-supervisor-workflow-dispatcher</string>
  <string>--config</string>
  <string>/Users/sam.zhang/.codex-supervisor/config.yaml</string>
  <string>--dispatcher-id</string>
  <string>launchd:workflow-dispatcher</string>
</array>
```

Agent-facing calls must stay non-blocking:

- `submit` reserves a durable job row and returns a `job_id`.
- `poll` reads ledger status only; it never writes request files, spawns a
  worker, or calls `WorkflowJobDispatcher.run_once`.
- `catch-up` reads the event tail for the caller-owned cursor.
- The dispatcher claims `reserved` or `request_written` jobs, writes requests,
  spawns workers, heartbeats leases, and reaps stale leases.

Useful operator commands (JSON is the default automation surface; TOON-lite
remains the human-readable default when `--json` is omitted):

```bash
codex-supervisor-axi
codex-supervisor-axi --json submit --task-id <task> --run-id <run> --intent "<intent>" --client-token <stable-token>
codex-supervisor-axi --json poll <job_id>
codex-supervisor-axi --json catch-up <run_id> --last-event-id <event_id>
codex-supervisor-axi --json trends --task-class source_change --gate outcome_review
```

MCP `run_dual_agent_workflow`, `poll_dual_agent_workflow_job`, and
`catch_up_dual_agent_workflow` remain available as a compatibility shim over
the same durable ledger core. They are non-blocking and equivalent to the AXI
JSON commands above.
