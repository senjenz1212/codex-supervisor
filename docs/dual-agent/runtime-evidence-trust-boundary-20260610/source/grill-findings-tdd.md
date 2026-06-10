# TDD Grill Findings

### Finding 1

Status: resolved

Concern: Helper-only tests could miss the public workflow boundary where caller receipts enter through `tool_receipts`.

Resolution: The TDD plan starts with a `run_dual_agent_workflow` boundary test that supplies forged receipts and asserts both gate blocking and downgrade events.

### Finding 2

Status: resolved

Concern: The command confinement test might prove rejection but not prove the command was never executed.

Resolution: The `python -c` test writes a marker path if executed and asserts the marker remains absent.

### Finding 3

Status: resolved

Concern: Environment scrub tests could pass while pytest execution is broken for honest runs.

Resolution: The plan pairs env inspection with allowlisted pytest pass/fail tests and the existing honest execution gate regression.
