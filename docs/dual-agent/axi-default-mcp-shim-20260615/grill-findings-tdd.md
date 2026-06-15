# TDD Grill Findings

### Finding 1 - First proofs must hit public surfaces
Status: resolved
Evidence: The TDD plan uses AXI `main(...)` and MCP tool/API boundaries for the
first RED tests rather than inspecting helper functions only.
Resolution: Helper-only assertions are allowed only as supporting checks after
public AXI/MCP calls establish the behavior.

### Finding 2 - Non-blocking MCP must be tested as absence of side effects
Status: resolved
Evidence: `test_mcp_compatibility_run_workflow_help_prefers_axi_json_recovery`
requires no request file and a forbidden synchronous runner.
Resolution: The test explicitly protects against inline phase execution and
request-writing regressions from a legacy MCP compatibility call.

### Finding 3 - Docs test must not rewrite historical evidence
Status: resolved
Evidence: The documentation test targets current default docs and skill
instructions, not archived `docs/dual-agent/**` transcripts or historical
analysis.
Resolution: Historical records remain intact; only current operating guidance
is migrated.

### Finding 4 - JSON default and TOON measurement can coexist
Status: resolved
Evidence: The TDD plan requires AXI help to prefer `--json` while existing
TOON-lite output tests remain green.
Resolution: The slice changes automation defaults and docs, not the measured
format implementation or observational metrics.
