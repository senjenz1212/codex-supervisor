## Slice 1: Label Reviewer Availability Without Weakening Production

Priority: P0

Scope: Add row-level labels for full_roster_available, reviewer_infrastructure_unavailable, panel_missing_verdict_block, and panel_quality_reject while preserving the fail-closed production decision.

Acceptance Criteria:
- [ ] Missing reviewer verdict blocks production acceptance.
- [ ] Missing reviewer verdict is labeled panel_missing_verdict_block, not panel_quality_reject.
- [ ] Fully available rejecting panel is labeled panel_quality_reject.
- [ ] Hidden oracle material is absent from reviewer diagnostics.

PRD promise: P1, P2, P4

## Slice 2: Guard Panel Marginal Metrics By Full-Roster Availability

Priority: P0

Scope: Update aggregate panel marginal reporting so FAR/TAR comparisons compute only over fully available reviewer rows and otherwise return an unavailable or insufficient-availability status.

Acceptance Criteria:
- [ ] Panel marginal refuses when full_roster_available row count is zero.
- [ ] Available rows keep FAR/TAR denominators explicit.
- [ ] Report-only invariants remain false for calibration output.

PRD promise: P3

## Slice 3: Add Diagnostics And Codex-Only Calibration Labeling

Priority: P1

Scope: Add reviewer-0 infrastructure diagnostics and an explicit Codex-only calibration mode that cannot be labeled as full-panel evidence.

Acceptance Criteria:
- [ ] Reviewer-0 infrastructure failure records reason, recoverability, and transcript or receipt hash evidence.
- [ ] Codex-only calibration emits codex_only_calibration.
- [ ] Codex-only calibration cannot produce full-panel evidence or policy proposals.

PRD promise: P4, P5
