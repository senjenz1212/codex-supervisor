# PRD Grill Findings: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

### Finding 1: MCP adapter and lifecycle service must remain separate

status: resolved

Concern: An extraction plan could accidentally propose moving MCP registration
or changing public tool names while trying to isolate durable job logic. That
would turn a behavior-preserving cleanup into a transport migration.

Resolution: P2 requires MCP methods to stay in
`mcp_tools/codex_supervisor_stdio.py` as thin adapters. The proposed service
module owns request construction, idempotency, detached spawn, poll
reconciliation, and catch-up payload assembly, while MCP registration remains
where clients already look.

### Finding 2: Terminal outcome authority must be explicit

status: resolved

Concern: Poll currently treats the ledger terminal outcome as authoritative
when the result file is missing or disagrees. If the extraction plan misses
that rule, a later refactor could accidentally trust stale cache files.

Resolution: P1 and P3 require the design doc to inventory terminal-outcome
reconciliation and name tests that prove ledger-wins behavior, result-file
fallback, discrepancy events, and CLI terminal persistence.

### Finding 3: Read-only workers must not write the plan independently

status: resolved

Concern: The requested fan-out uses four workers, but workers must be
read-only investigators. If workers write files, the provenance boundary gets
muddy and the lead no longer owns the synthesis.

Resolution: P4 says workers produce findings only. The lead owns the design
doc and the gated transcript must show worker receipts with read-only
permission mode.

### Finding 4: A doc-only slice still needs a source-change guard

status: resolved

Concern: Because the plan mentions `supervisor/state.py`, `mcp_tools`, and the
CLI, it could drift into opportunistic source edits.

Resolution: P5 makes source refactoring explicitly forbidden in this run. The
implementation plan and done criteria limit the diff to documentation and
planning artifacts.
