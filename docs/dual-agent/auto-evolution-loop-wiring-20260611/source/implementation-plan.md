# Implementation Plan

## Scope

Wire Phase W only: triggers, daemon cadences, and operator verbs for already-verified mechanisms. Do not change policy authority, default autonomy, immutable paths, or gate semantics.

## Steps

1. Add daemon task wrappers in `supervisor/autoresearch/daemon_tasks.py`.
2. Register `autoresearch_runner` and `weekly_p11_audit` restartable tasks in `daemon.py`.
3. Add `runner_interval_s` to AutoResearch config and example config.
4. Add candidate-run queries for weekly P11 audit in SQLite and Postgres state lanes.
5. Call experiment drafting and lesson feedback from `_workflow_result`.
6. Call policy proposal derivation from AutoResearch report emission when the report has a derivable accepted record.
7. Expose experiment list/generate/activate in MCP and AXI.
8. Add AXI proposal-aware approve/deny while preserving generic decisions without `--proposal-id`.
9. Add request-path guard coverage.
10. Run focused and regression test suites.

## Files / Modules To Touch

- `supervisor/autoresearch/daemon_tasks.py`
- `daemon.py`
- `supervisor/config.py`
- `config.example.yaml`
- `supervisor/state.py`
- `supervisor/postgres_state.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_axi.py`
- `supervisor/autoresearch/orchestrator.py`
- `supervisor/autoresearch/policy_evolution.py`
- `tests/test_autoresearch_generator.py`
- `tests/test_autoresearch_daemon_tasks.py`
- `tests/test_autoresearch_policy_evolution.py`
- `tests/test_supervisor_lessons.py`
- `tests/test_codex_supervisor_axi.py`
- `tests/test_request_path_non_execution_guard.py`

## Verification

- Phase W focused suite: generator, daemon tasks, policy evolution, lessons, AXI, request-path guard.
- Regression suite: policy overlay, quality trends, MCP stdio, workflow driver, AutoResearch, terminal bench.
- Static checks: `git diff --check` and `py_compile` on touched modules.

## Risks

- A request-path verb could accidentally call a workflow phase; the guard test makes that fail loudly.
- Auto-derivation could create noisy skipped events for ordinary reports; the trigger is narrowed to derivable accepted records.
- Lesson feedback could become confused with acceptance; every event is advisory and gate authority remains unchanged.

## Traceability

- P1 -> test_workflow_finalization_generates_autoresearch_draft_from_recurring_failures, test_workflow_finalization_below_threshold_generates_no_draft.
- P2 -> test_autoresearch_runner_tick_executes_only_activated_experiments, test_autoresearch_runner_tick_respects_weekly_cap.
- P3 -> test_autoresearch_report_acceptance_auto_derives_overlay_proposal.
- P4 -> test_weekly_p11_audit_tick_writes_scheduled_audit_event.
- P5 -> test_axi_experiments_activate_transitions_draft_to_runnable, test_axi_policy_approve_proposal_applies_hashes_and_rollback_pointer, test_axi_policy_deny_proposal_records_denial_without_apply.
- P6 -> test_workflow_finalization_records_lesson_feedback_and_retires_recurring_lesson.
- P7 -> test_new_mcp_and_cli_verbs_do_not_drive_dispatcher_or_spawn_workers.

## Rollout

The daemon cadence is inert unless the daemon runs. Experiment execution still requires operator activation, and policy changes still require operator approve or deny. Existing MCP tools and manual policy proposal generation remain available.
