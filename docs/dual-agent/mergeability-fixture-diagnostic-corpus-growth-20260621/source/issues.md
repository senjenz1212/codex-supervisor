# Issues

## Slice 1: Grow Diagnostic Oracle-Positive Fixture Coverage

Priority: high

### PRD Promise

P1, P2, and P3.

### Public Boundary For First RED Test

Use the fixture measurement runner/report interface that produces the paired acceptance report. The first test must call the public runner or paired pilot path and inspect the exported report shape, not a helper-only manifest function.

### Scope:

Add diagnostic oracle-positive fixture candidates tied to the Slice 1A not-matched full-gate result. Preserve positive controls, negative controls, false-accept traps, hidden oracle isolation, and produced baseline compatibility.

### Acceptance Criteria:

- [ ] The corpus growth rationale records that Slice 1A had S_full true-accept rate zero while S_probe true-accept rate was one.
- [ ] The grown fixture corpus has increased oracle-positive diagnostic coverage while retaining at least one positive control, one negative control, and one public-pass hidden-fail false-accept trap.
- [ ] Public packets and public worktrees for new candidates exclude hidden oracle material, protected paths, final_score, oracle_accept, expected outcomes, and hidden test artifacts.

### Blocked By

Slice 1A commit b7c9ba41, because this slice uses that measured report as the diagnostic input.

## Slice 2: Re-run Fixture Measurement With Diagnostic Reporting

Priority: high

### PRD Promise

P1, P4, and P5.

### Public Boundary For First RED Test

Use the same fixture measurement runner/report interface after Slice 1 fixture growth lands. The test should inspect the exported paired acceptance report and calibration summary, not private report assembly helpers.

### Scope:

Re-run the fixture measurement with produced single-agent baseline receipts and configured panel behavior. Export a report that records n_good, n_bad, S_probe accepted false accepts, S_full accepted false accepts, TAR loss, matched-TAR status, confidence intervals, and report-only invariants.

### Acceptance Criteria:

- [ ] The measurement report records n_good, n_bad, S_probe accepted false accepts, S_full accepted false accepts, TAR loss, matched-TAR status, and confidence intervals.
- [ ] The report keeps metric_applyable=false, improvement_claim_allowed=false, policy_mutated=false, gate_advanced=false, and no best-of-K improvement language.
- [ ] The report includes the diagnostic growth rationale so future readers can trace the corpus additions back to Slice 1A.
- [ ] The report remains calibration-only even if S_full false accepts improve or true-accept loss changes.

### Blocked By

Slice 1 must land first so the report can measure the grown diagnostic corpus.
