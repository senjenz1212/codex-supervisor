# TDD Grill Findings: Report-Only Lead Tool Policy

### Finding 1: Command tests alone would miss the P11 safety property

status: resolved

The command boundary proves the lead receives the necessary tools, but it cannot
prove that report-only artifacts still require real changed files.

resolution: Add workflow-driver tests for both the accepted report artifact path
and the blocked receipt-without-changed-file path.

### Finding 2: Workflow tests alone would miss the original invocation bug

status: resolved

The original failure was at the Claude Code command boundary. A workflow-driver
test that bypasses command construction would not fail for the missing
`--allowedTools` argument.

resolution: Add `build_claude_lead_command` tests for report-only and normal
execution requests.

### Finding 3: Test names must preserve PRD traceability

status: resolved

The TDD plan needs concrete test names that map directly to the PRD promise
contracts, otherwise an accepted implementation could drift away from the user
visible promises.

resolution: Each test section includes a `Maps to:` line for P1, P2, or P3, and
the implementation plan repeats the same mapping.
