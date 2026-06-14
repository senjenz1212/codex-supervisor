# TDD Plan

## Boundary Tests First

1. `tests/test_codex_supervisor_axi.py::test_axi_experiments_park_drains_draft_with_audit_event`
   - RED: `experiments park` command does not exist.
   - GREEN: command parks a draft, writes `autoresearch_experiment_parked`, and excludes it from open queue pressure.

2. `tests/test_autoresearch_generator.py::test_parked_autoresearch_experiment_cannot_be_activated_or_run`
   - RED: state has no park status transition.
   - GREEN: parked rows remain parked on activation attempts and runner ignores them.

3. `tests/test_policy_overlay.py::test_task_class_overlay_applies_only_to_matching_task_class`
   - RED: overlay loader only supports global keys.
   - GREEN: scoped task-class guidance appears only for matching `lesson_task_class`.

4. `tests/test_policy_overlay.py::test_task_class_freeze_suppresses_overlay_guidance_without_blocking_gate`
   - RED: no freeze field.
   - GREEN: no policy block is appended, snapshot records `overlay_frozen=true`, and gate kwargs are still returned.

5. `tests/test_autoresearch_policy_evolution.py::test_policy_proposal_without_empty_floor_is_non_applyable`
   - RED: accepted reports can derive proposal without empty-floor comparison.
   - GREEN: missing empty-floor evidence produces no applyable proposal and records a non-applyable reason.

6. `tests/test_policy_overlay.py::test_empty_floor_rebaseline_due_event_is_observational`
   - RED: no rebaseline scheduler helper exists.
   - GREEN: helper emits `policy_empty_floor_rebaseline_due` with `automatic_policy_mutation=false`.

7. `tests/test_quality_trends.py::test_trends_reports_d1_d2_insufficient_data`
   - RED: trend row exposes raw counts only.
   - GREEN: response includes decision-status fields showing insufficient samples.

## Regression Tests

- Existing runtime evidence tests stay green.
- Existing AutoResearch daemon tests stay green.
- Existing Postgres lane tests stay green or skip only when DSN is absent.

