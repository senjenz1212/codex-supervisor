# TDD Plan: Auto-Evolution Loop Wiring

## Test Strategy

The first RED tests hit the public boundaries users or operators rely on: workflow finalization, daemon ticks, AutoResearch report emission, AXI command handling, and MCP/CLI request paths. Helper tests are allowed only after those public-boundary tests exist.

## test_workflow_finalization_generates_autoresearch_draft_from_recurring_failures

- Test: `tests/test_autoresearch_generator.py::test_workflow_finalization_generates_autoresearch_draft_from_recurring_failures`
Maps to: Slice W1, P1.
- Boundary: `CodexSupervisorMcpAPI._workflow_result`.
- Arrange: Seed three matching taxonomy failures for one task class and gate.
- RED: No draft is generated before the finalization hook is wired.
- GREEN: Exactly one draft row appears, with provenance event ids and status `draft`.

## test_workflow_finalization_below_threshold_generates_no_draft

- Test: `tests/test_autoresearch_generator.py::test_workflow_finalization_below_threshold_generates_no_draft`
Maps to: Slice W1, P1.
- Boundary: `CodexSupervisorMcpAPI._workflow_result`.
- Arrange: Seed fewer than the configured recurrence threshold.
- RED: Below-threshold input incorrectly drafts an experiment.
- GREEN: No experiment is drafted.

## test_autoresearch_runner_tick_executes_only_activated_experiments

- Test: `tests/test_autoresearch_daemon_tasks.py::test_autoresearch_runner_tick_executes_only_activated_experiments`
Maps to: Slice W2, P2.
- Boundary: `AutoResearchRunnerTask.tick_once`.
- Arrange: One draft and one activated experiment.
- RED: Draft experiment is executed or runnable experiment is ignored.
- GREEN: Only the activated experiment is passed to the runner.

## test_autoresearch_runner_tick_respects_weekly_cap

- Test: `tests/test_autoresearch_daemon_tasks.py::test_autoresearch_runner_tick_respects_weekly_cap`
Maps to: Slice W2, P2.
- Boundary: `run_runnable_autoresearch_experiments` via daemon task.
- Arrange: Seed used weekly capacity.
- RED: Tick executes despite exhausted weekly cap.
- GREEN: Tick executes zero additional experiments.

## test_weekly_p11_audit_tick_writes_scheduled_audit_event

- Test: `tests/test_autoresearch_daemon_tasks.py::test_weekly_p11_audit_tick_writes_scheduled_audit_event`
Maps to: Slice W2, P4.
- Boundary: `WeeklyP11AuditTask.tick_once`.
- Arrange: Fake candidate provider and auditor.
- RED: No audit is invoked.
- GREEN: Tick calls the auditor and returns observational results.

## test_autoresearch_report_acceptance_auto_derives_overlay_proposal

- Test: `tests/test_autoresearch_policy_evolution.py::test_autoresearch_report_acceptance_auto_derives_overlay_proposal`
Maps to: Slice W3, P3.
- Boundary: `run_autoresearch_fixture`.
- Arrange: Fixture contains accepted evaluator-backed overlay candidate with positive metric delta.
- RED: Accepted report emits no proposal.
- GREEN: One `autoresearch_policy_proposal_created` event exists with source `autoresearch_deriver`.

## test_workflow_finalization_records_lesson_feedback_and_retires_recurring_lesson

- Test: `tests/test_supervisor_lessons.py::test_workflow_finalization_records_lesson_feedback_and_retires_recurring_lesson`
Maps to: Slice W1, P6.
- Boundary: `_workflow_result`.
- Arrange: Inject a lesson and recur its taxonomy over three finalizations.
- RED: Counters do not update or retired lesson is still selected.
- GREEN: Counts update; retired lesson id is absent from future lesson selection.

## test_axi_experiments_activate_transitions_draft_to_runnable

- Test: `tests/test_codex_supervisor_axi.py::test_axi_experiments_activate_transitions_draft_to_runnable`
Maps to: Slice W4, P5.
- Boundary: `codex_supervisor_axi.main`.
- Arrange: Seed a draft experiment.
- RED: CLI activation is missing or leaves the experiment as draft.
- GREEN: CLI activation changes status to `runnable` and emits help.

## test_axi_policy_approve_proposal_applies_hashes_and_rollback_pointer

- Test: `tests/test_codex_supervisor_axi.py::test_axi_policy_approve_proposal_applies_hashes_and_rollback_pointer`
Maps to: Slice W4, P5.
- Boundary: `codex_supervisor_axi.main`.
- Arrange: Seed a proposal event and overlay candidate.
- RED: Approval does not apply the recorded diff or misses hashes.
- GREEN: Approval applies exactly the recorded diff and records rollback.

## test_axi_policy_deny_proposal_records_denial_without_apply

- Test: `tests/test_codex_supervisor_axi.py::test_axi_policy_deny_proposal_records_denial_without_apply`
Maps to: Slice W4, P5.
- Boundary: `codex_supervisor_axi.main`.
- Arrange: Seed a proposal event and overlay candidate.
- RED: Denial mutates the overlay.
- GREEN: Denial records denial and applies nothing.

## test_new_mcp_and_cli_verbs_do_not_drive_dispatcher_or_spawn_workers

- Test: `tests/test_request_path_non_execution_guard.py::test_new_mcp_and_cli_verbs_do_not_drive_dispatcher_or_spawn_workers`
Maps to: Slice W4, P7.
- Boundary: new MCP methods and AXI verbs.
- Arrange: Monkeypatch `WorkflowJobDispatcher.run_once` and `subprocess.Popen` to fail if called.
- RED: Any request-path verb calls dispatcher or spawn seam.
- GREEN: New request-path verbs complete without invoking either seam.

## Regression Suites

- `tests/test_policy_overlay.py`
- `tests/test_quality_trends.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_autoresearch.py`
- `tests/test_terminal_bench_eval.py`

## Translation Audit

- P1 maps to RED 1 and RED 2.
- P2 maps to RED 3 and RED 4.
- P3 maps to RED 6 and existing negative policy derivation tests.
- P4 maps to RED 5.
- P5 maps to RED 8 and RED 9.
- P6 maps to RED 7.
- P7 maps to RED 10.
