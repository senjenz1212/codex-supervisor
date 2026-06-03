# TDD Grill Findings: Agentic Eval Quality Signals

### Finding 1: The First RED Test Must Hit The Runner Boundary

status: resolved

The plan starts at `agentic_eval_runner`, the public boundary that builds rows
and exports reports. Helper assertions are acceptable only after the boundary
regression exists.

### Finding 2: A Corrected Number Without Divergence Is Not Enough

status: resolved

The TDD plan asserts both the authoritative field and the reported conflicting
field. This prevents future cleanups from making bad cassette metrics invisible
again.

### Finding 3: Existing Bridge Report Must Be Replayed

status: resolved

The plan includes regeneration of the committed bridge report, so the artifact
that demonstrated the bug also demonstrates the fix.
