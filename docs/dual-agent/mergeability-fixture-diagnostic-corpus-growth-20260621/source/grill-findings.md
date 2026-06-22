# PRD Grill Findings

## Findings

### Finding 1: Positive Growth Could Shift Denominators

Status: resolved

The corpus could appear healthier simply because more positive examples were added. Resolution: the PRD requires separate n_good and n_bad reporting, S_probe and S_full false accepts, TAR loss, matched-TAR status, and confidence intervals.

### Finding 2: Panel Strictness Could Be Mistaken For Oracle Truth

Status: resolved

The panel is the measured supervisor component, not the source of truth. Resolution: hidden oracle labels remain the grading source and are applied only after public decisions are frozen.

### Finding 3: New Fixtures Could Leak Hidden Oracle Material

Status: resolved

Richer fixture metadata can accidentally cross the public boundary. Resolution: P3 requires reviewer packets and public worktrees to exclude hidden oracle paths, final_score, oracle_accept, expected outcomes, and protected artifacts.

### Finding 4: Growth Could Miss The Slice 1A Blocker

Status: resolved

The slice could grow generic examples without addressing the measured failure. Resolution: P1 anchors growth to the Slice 1A full-gate not-matched result where S_full TAR was zero and S_probe TAR was one.

## Waivers

No waivers. Each finding is reflected in the PRD and must be carried into the issue slices and TDD plan.
