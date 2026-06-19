## Findings

### Finding 1: Authored Corpus Overclaim

Status: resolved

The original plan could overclaim by treating a larger authored corpus as real agent evidence. Resolution: the PRD explicitly keeps all outputs calibration-only and non-applyable until live generation and powered evaluation exist.

### Finding 2: Shallow Task-Class Coverage

Status: resolved

The corpus expansion could be shallow if it only adds more calculator candidates. Resolution: P1 and P2 require task_class coverage and per-class positive and negative controls.

### Finding 3: Hidden No-Regression Result

Status: resolved

A no-regression check could become a hidden helper assertion nobody sees in reports. Resolution: P3 requires no-regression status in the paired report and exported artifacts.

### Finding 4: Oracle-Material Leakage

Status: resolved

Hidden oracle material could leak through richer fixture metadata. Resolution: P1 repeats oracle isolation as a promise and tests must inspect serialized report and reviewer evidence.

## Waivers

No waivers. Each finding is reflected in the PRD and must be carried into the TDD plan.
