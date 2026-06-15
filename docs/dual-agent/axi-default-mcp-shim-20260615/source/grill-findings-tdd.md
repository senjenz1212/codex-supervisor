# TDD Grill Findings

### Finding 1 - Public-boundary tests come first
Status: resolved
Evidence: The TDD plan uses AXI CLI and MCP tool/API calls for first proofs.
Resolution: Helper assertions only support public-boundary behavior.

### Finding 2 - No-inline-execution needs side-effect checks
Status: resolved
Evidence: The MCP compatibility test forbids the synchronous runner and checks
that no request file exists after reservation.
Resolution: The test protects the transport-timeout failure mode directly.

### Finding 3 - Docs tests must target live guidance
Status: resolved
Evidence: The docs test names current AXI docs, the new-chat how-to, and the
dual-agent skill.
Resolution: Historical `docs/dual-agent/**` transcripts are not used as
current default prompt sources.

### Finding 4 - JSON default must not erase TOON-lite tests
Status: resolved
Evidence: The AXI help test changes automation hints; it does not remove
TOON-lite output coverage.
Resolution: Existing AXI output-format tests stay in the regression suite.
