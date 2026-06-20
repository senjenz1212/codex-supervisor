# PRD Grill Findings

### Finding 1 -- The runner must produce decisions, not decorate supplied labels

Status: resolved

Concern: A runner that merely wraps caller-provided `arm_decisions` would repeat the scaffold-only bridge problem and could still emit convincing report fields without executing public probes.

Resolution: The PRD now names the executable runner as the public boundary. It requires real patch application, real public command execution, reviewer packet construction, and a frozen decision artifact before hidden oracle outcomes are attached.

### Finding 2 -- Public and hidden materials need structural separation

Status: resolved

Concern: A local fixture can accidentally make hidden oracle files available to the public probe or reviewer packet if the runner copies the entire fixture tree.

Resolution: The PRD requires a public worktree that excludes protected hidden paths, excludes hidden commands from reviewer packets, and treats oracle leakage as a failing boundary condition before metrics are produced.

### Finding 3 -- Reviewer unavailability must not become implicit S_full signal

Status: resolved

Concern: The full-gate arm is only meaningful when a reviewer panel actually returns a decision; unavailable infrastructure must not be counted as accept or copied from S_probe.

Resolution: The PRD requires `reviewer_panel_unavailable` status for S_full when no panel is configured and requires tests that preserve S_probe/S_full disagreement when a panel is injected.

### Finding 4 -- Fixture evidence is plumbing evidence, not benchmark improvement

Status: resolved

Concern: A passing fixture report could be misread as a real SWE-bench or supervisor performance result.

Resolution: Report-only invariants remain mandatory, live SWE-bench fetching and generation are out of scope, and the runner output is described as producer-path proof rather than improvement evidence.
