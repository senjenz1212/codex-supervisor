# PRD Grill Findings

## Status

All findings are resolved in the PRD and issue pack.

## Findings

### G1. Product Name Drift

Finding: The repo and scaffold are Codex-shaped, but the user requirement is
Claude Code now and Codex later.

Resolution: Keep the repo name. Define the product as Agent Supervisor and add a
Target Agent Adapter boundary. Claude Code is v1. Codex is future-compatible.

### G2. Runtime and Target Were Blurred

Finding: Claude Agent SDK was being described as the supervisor brain and target
runtime, which could couple decision logic to Claude Code integration.

Resolution: Separate `DecisionRuntime` from `TargetAgentAdapter`. Claude Agent
SDK remains the default decision runtime only.

### G3. Replay Must Precede Enforcement

Finding: Enforcement without replay would make false positives difficult to
debug and tune.

Resolution: Replay CLI and run snapshots are required before soft steering or
hook enforcement moves beyond advise or shadow.

### G4. Permission Requests Need Public-Boundary Coverage

Finding: Permission events were mentioned as hooks, but not promoted into issue
acceptance criteria.

Resolution: Hook issue claims `PermissionRequest` explicitly and tests through
the HTTP hook boundary.

### G5. Secrets Need a Pipeline, Not a Reminder

Finding: Redaction was listed as a risk but not as a product promise.

Resolution: Add P6 and require tests that verify storage and notification
payloads are redacted.
