# TDD Plan

## Issue 1: Fixture roster-selection guard

RED 1: Public-boundary test calls `run_fixture_panel_produced_baseline_measurement` with a configured two-reviewer panel and asserts:
- `roster_selection_guard.roster_selection_allowed is False`
- `roster_selection_guard.evidence_scope == "fixture_diagnostic_only"`
- `roster_selection_guard.required_evidence` names real/disagreement candidate evidence
- policy proposal derivation returns `[]`

GREEN 1: Add a report-block builder and wire it into paired fixture reports.

RED 2: Public-boundary test creates a saturated reviewer-agreement report and asserts the guard records a saturation/non-selection reason.

GREEN 2: Derive a saturated-agreement note from `configured_reviewer_panel.inter_reviewer_agreement`.

## Issue 2: Codex-only calibration stays calibration-only

RED 1: Public-boundary test calls `run_paired_acceptance_pilot` with `codex_only_calibration=True` and asserts:
- `configured_reviewer_panel.report_mode == "codex_only_calibration"`
- `configured_reviewer_panel.full_panel_evidence_allowed is False`
- `roster_selection_guard.roster_selection_allowed is False`
- `roster_selection_guard.codex_only_can_select_roster is False`

GREEN 1: Reuse the same guard builder for Codex-only calibration mode.

## Issue 3: Bounded runner cache provenance

RED 1: Public-boundary test calls `run_bounded_parallel_panel_corpus` and asserts:
- `bounded_runner.cache_policy.reviewer_roster_ids_in_identity is True`
- `bounded_runner.cache_policy.option_hash_in_identity is True`
- `bounded_runner.cache_policy.changed_roster_recomputes is True`

GREEN 1: Add cache-policy metadata next to existing roster ids and option hash.

RED 2: Existing stale-checkpoint test remains green, proving changed identity recomputes.

GREEN 2: No extra implementation unless the existing behavior regresses.

## TDD Grill Findings

Resolved: The first tests exercise the public runner seam, not helper-only methods.

Resolved: The tests verify forbidden outcomes by checking machine-readable report authority flags and policy derivation, not prose.

Resolved: No test uses live reviewers, hidden oracle material, or model APIs.
