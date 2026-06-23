# Grill Findings

### Finding 1: Diagnostic Framing

Status: resolved

The PRD must frame this as a diagnostic smoke, not a benchmark improvement result. The promise contracts require report-only invariants and accept unavailable or not_matched panel marginal status when the reason is explicit.

### Finding 2: Oracle Boundary

Status: resolved

Hidden oracle pass or fail cannot be a reviewer-assessed blocker because the reviewer packet must remain public-only. The PRD keeps oracle outcomes as post-decision scoring evidence and keeps reviewer decisions limited to public evidence.

### Finding 3: Cursor Isolation Proof

Status: resolved

Cursor isolation must be proven by diagnostics, not inferred from the recent code change. The TDD plan starts at the public diagnostic boundary and checks isolated worktree evidence plus absence of source cursor_modified_worktree failures.

### Finding 4: Reviewer Availability Classification

Status: resolved

Missing reviewer verdicts must be separated from quality rejection. The issue and tests require unavailable infrastructure or configuration to block S_full availability rather than creating an accept or reject label without evidence.
