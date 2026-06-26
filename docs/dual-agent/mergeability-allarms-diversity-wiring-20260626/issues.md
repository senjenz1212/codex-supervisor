# Mergeability All-Arms Diversity Wiring Issues

## Tracer Bullet 1 — Surface reviewer independence in the official all-arms diagnostic

### 1. Parent

Local task id: `mergeability-allarms-diversity-wiring-20260626`.

### 2. What To Build

Wire the official all-arms diagnostic report to emit the existing inter-reviewer agreement, leave-one-reviewer-out, and effective vote estimate metrics from the reviewer rows it already assembles.

### 3. PRD Promise

P1.

### 4. Public Boundary

`_build_official_all_arms_diagnostic_report` return dict.

### 5. Chosen Seam

The report assembly seam beside `reviewer_provenance` and `generator_disjointness`.

### 6. Representative Action

Build an official-report-shaped fixture with two candidates and two reviewers, call `_build_official_all_arms_diagnostic_report`, and inspect the returned dict.

### 7. Allowed Outcomes

The returned dict includes `inter_reviewer_agreement`, `leave_one_reviewer_out`, and `effective_vote_estimate`; the metrics use the same candidate-pool hash; zero-error or no-reviewer evidence stays unavailable.

### 8. Forbidden Outcomes

Do not fork the metric logic, do not mark empty reviewer evidence as computed, do not mutate oracle/candidate/baseline/panel behavior, and do not build the autonomous benchmark-to-policy bridge.

### 9. TDD Plan

First RED: `tests/test_allarms_diversity.py::test_all_arms_report_includes_independence_metrics` through `_build_official_all_arms_diagnostic_report`.

Minimal GREEN: import and call the existing helpers, derive only their required existing input shapes, and insert the results beside reviewer provenance and generator disjointness.

### 10. Acceptance Criteria

- [ ] A two-reviewer official all-arms fixture returns populated `inter_reviewer_agreement`, `leave_one_reviewer_out`, and computed `effective_vote_estimate`.
- [ ] A no-reviewer fixture returns empty pairwise agreement, unavailable leave-one-out, and unavailable effective vote estimate.
- [ ] A zero-reviewer-error fixture keeps `effective_vote_estimate` unavailable with `zero_oracle_grounded_reviewer_errors`.
- [ ] The report exposes one candidate-pool hash shared by the three independence metrics.
- [ ] Existing report-only authority flags remain false.

### 11. Blocked By

None. This is independent wiring after the panel slice.
