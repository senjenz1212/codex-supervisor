# Grill Findings: Report-Only Lead Tool Policy

### Finding 1: A permission fix could accidentally weaken P11

status: resolved

The issue is not that P11 rejects report-only artifacts; it is that the lead
cannot author the bounded artifact in the report-only execution request. The
implementation therefore changes only the report-only Claude Code command
permission mode plus allowed tools, and adds workflow-driver tests proving P11
still blocks receipt-only claims.

resolution: Keep `supervisor/dual_agent_workflow.py` behavior intact and add
negative coverage for the no-changed-file report receipt case.

### Finding 2: Broad shell access would be larger than the product promise

status: resolved

The report-only lead needs filesystem reads, report writes, git diff/status, and
focused pytest receipts. It does not need destructive or unrestricted shell
patterns.

resolution: Use a tuple with named tools and narrow Bash patterns; assert that
`Bash(*)` and `Bash(rm *)` are absent.

### Finding 3: The policy branch must not affect ordinary code tasks

status: resolved

Report-only execution is a special case. Normal implementation tasks should not
inherit the report-only allowed-tools list merely because they use the execution
gate.

resolution: Require both an execution gate and explicit report-only plus
docs/report deliverable markers before appending `--allowedTools`.
