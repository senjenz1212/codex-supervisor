# PRD Grill Findings

### Finding 1: Overlay Scope Could Become A General Patch Channel

Status: resolved

Concern: If policy proposals can target arbitrary files, AutoResearch becomes an indirect prompt/config mutation system instead of a constrained overlay.

Resolution: Policy evolution targets are normalized and rejected unless they equal `.supervisor/policy-overlay.yaml`.

### Finding 2: Liveness Without Hash Attribution Is Not Replayable

Status: resolved

Concern: A changed lead instruction without a recorded overlay hash would make trend metrics impossible to interpret later.

Resolution: Gate start records `supervisor_policy_overlay_snapshot` with overlay path, content hash, block hash, and proposal id; trend rows copy the active attribution.

### Finding 3: Regression Handling Could Bypass Human Approval

Status: resolved

Concern: A rollback detector that applies changes directly would collapse the two-touchpoint safety model.

Resolution: Regression verification only drafts rollback proposals and emits events; apply still requires operator approval.

### Finding 4: Lesson Hygiene Could Delete Useful History

Status: resolved

Concern: Retiring stale lessons by deleting rows would weaken replay and trend analysis.

Resolution: Retired lessons retain ledger rows and are excluded only from future injection selection.
