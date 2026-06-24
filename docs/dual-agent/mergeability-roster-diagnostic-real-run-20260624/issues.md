# Issues

## Issue 1: Add fixture roster-selection guard to mergeability reports

## What to build

Add a report-level guard that states fixture-only roster diagnostics cannot select or drop reviewers. The guard must be emitted by the existing paired fixture measurement and bounded runner paths.

## PRD Promise

- Promise IDs: P1, P3, P5
- Public boundary for first RED test: `mergeability_fixture_measurement`
- Chosen seam/interface: `run_fixture_panel_produced_baseline_measurement`
- Representative action: run the fixture measurement with a configured two-reviewer panel.
- Allowed outcomes: diagnostic metrics emit, roster selection is blocked, required real/disagreement evidence is named.
- Forbidden outcomes: `roster_selection_allowed=true` or a recommendation to drop reviewers from fixture-only evidence.

## Acceptance Criteria

- [ ] Fixture measurement reports include `roster_selection_guard.roster_selection_allowed=false`.
- [ ] Saturated agreement is reported as non-selection evidence.
- [ ] Policy proposal derivation returns empty.

## Blocked By

None - can start immediately.

## Issue 2: Preserve Codex-only calibration as non-full-panel evidence

## What to build

Ensure Codex-only calibration reports remain available as calibration evidence while explicitly blocking full-panel claims and roster selection.

## PRD Promise

- Promise IDs: P2
- Public boundary for first RED test: `mergeability_fixture_measurement`
- Chosen seam/interface: `run_paired_acceptance_pilot` with `ConfiguredReviewerPanelOptions(codex_only_calibration=True)`
- Representative action: run Codex-only calibration with injected reviewer adapters.
- Allowed outcomes: Codex-only marginal may compute, `full_panel_evidence_allowed=false`, roster selection blocked.
- Forbidden outcomes: Codex-only calibration counts as full-panel evidence or selects the production roster.

## Acceptance Criteria

- [ ] `codex_only_calibration_panel` remains separate.
- [ ] `configured_reviewer_panel.report_mode=codex_only_calibration`.
- [ ] `roster_selection_guard.roster_selection_allowed=false`.

## Blocked By

Issue 1.

## Issue 3: Surface roster/cache provenance in bounded runner reports

## What to build

Expose the existing checkpoint identity semantics as report metadata so changed rosters are visibly not allowed to reuse stale reviewer evidence.

## PRD Promise

- Promise IDs: P4
- Public boundary for first RED test: `mergeability_fixture_measurement`
- Chosen seam/interface: `run_bounded_parallel_panel_corpus`
- Representative action: run bounded fixture report with configured reviewer options.
- Allowed outcomes: report records reviewer roster ids, option hash, and a cache policy saying roster/options changes invalidate checkpoints.
- Forbidden outcomes: report omits cache policy while reusing checkpointed reviewer results.

## Acceptance Criteria

- [ ] Bounded runner report includes cache invalidation policy.
- [ ] Existing checkpoint stale-identity behavior stays green.

## Blocked By

Issue 1.

## Coverage Index

- P1: Issue 1
- P2: Issue 2
- P3: Issue 1
- P4: Issue 3
- P5: Issue 1
