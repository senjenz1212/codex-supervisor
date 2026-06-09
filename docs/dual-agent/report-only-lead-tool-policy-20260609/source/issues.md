# Issues: Report-Only Lead Tool Policy

## Slice 1: Narrow report-only execution allowed tools

scope: Add a command-construction branch in
`supervisor/dual_agent_lead.py` that recognizes explicit report-only execution
requests, switches that branch away from unsafe global bypass, and appends the
narrow allowed-tools list needed to write reports, inspect diffs, and run
focused pytest receipts.

priority: high

PRD promises: P1, P2

acceptance criteria:

- [ ] A report-only execution request that names a docs/report deliverable adds
  `--allowedTools` with read, edit, write, git status, git diff, and pytest
  command patterns.
- [ ] The same report-only request uses a non-bypass permission mode so the
  allowed-tools list is the operative command policy.
- [ ] A normal execution request does not add the report-only allowed-tools list.
- [ ] The allowed list excludes broad destructive shell access such as `Bash(*)`
  or `Bash(rm *)`.

## Slice 2: Preserve P11 deliverable evidence for report artifacts

scope: Add workflow-driver regressions proving Vela-style report-only artifacts
are accepted only when the outcome changed file and receipt point at the report
deliverable.

priority: high

PRD promises: P3

acceptance criteria:

- [ ] A report-only workflow with changed file
  `docs/dual-agent/.../autoresearch-report.md` and a matching receipt passes
  P11.
- [ ] The same claimed report receipt with no changed file remains blocked at
  P11.
- [ ] Existing code-change deliverable evidence tests stay green.
