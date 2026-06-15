# PRD Grill Findings

### Finding 1 - AXI default must not become a benchmark claim
Status: resolved
Evidence: P4 explicitly forbids TOON or AXI performance claims without local
normalized evidence.
Resolution: The PRD frames this slice as an operating-boundary migration only.

### Finding 2 - MCP must remain compatible
Status: resolved
Evidence: P2 preserves the MCP public names and requires AXI recovery hints
instead of deletion.
Resolution: The issue slices update compatibility behavior and tests, not the
existence of the MCP tools.

### Finding 3 - Cross-surface retry behavior needs direct proof
Status: resolved
Evidence: P3 requires AXI-to-MCP, MCP-to-AXI, and catch-up equivalence tests.
Resolution: The TDD plan names public-boundary tests for each retry direction
and for event-tail equality.

### Finding 4 - Historical artifacts should not be rewritten
Status: resolved
Evidence: The PRD scopes docs/templates/current guidance, not archived
transcripts.
Resolution: The implementation plan targets current default instructions and
leaves old evidence projections alone unless they are live operating docs.
