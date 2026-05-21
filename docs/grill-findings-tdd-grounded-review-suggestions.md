# Grounded Review Suggestions TDD Grill Findings

## Status

All findings resolved in the issue plan and tests before GREEN implementation.

## Findings

### GRT1. First Proof Must Use the Supervisor Tool Boundary

Finding: A helper-only test for parsing `workdir` strings would not prove
Claude can access grounded workspace evidence through its actual tool surface.

Resolution: The first RED tests call `SupervisorToolAPI.read_workspace_snapshot`
and `SupervisorToolAPI.read_workspace_file`, the same tools exposed to the
Claude supervisor runtime.

### GRT2. Escape and Secret Cases Need Boundary Assertions

Finding: Positive-only snapshot tests could pass while unsafe path traversal or
secret leakage remains possible.

Resolution: The TDD plan includes explicit assertions that `../escape` fails
closed and secret material is absent from returned snapshot/file payloads.

### GRT3. Automatic Review Trigger Must Be Below Progress Streaming, Not Raw Tail

Finding: Enqueuing reviews directly from every rollout line would bypass the
CS8 noise filter.

Resolution: The review trigger is tested through `TelegramProgressStreamer`:
only a successfully delivered watched `task_complete` progress update enqueues
`review_updates`.

### GRT4. Agent Reviewer Needs Read-Only Tools Only

Finding: Reusing the drift skill's tool allowlist would expose
`inject_steering`, which contradicts CS9's advice-only promise.

Resolution: The `review_updates` decision uses a dedicated skill and a
read-only allowlist: rollout, metadata, workspace snapshot, workspace file, and
Telegram send.
