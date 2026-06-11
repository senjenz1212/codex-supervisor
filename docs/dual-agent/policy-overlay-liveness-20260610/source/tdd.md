# TDD Plan: Policy Overlay Liveness

## test_applied_overlay_changes_next_gate_instruction_and_records_hash

Maps to: Slice B1, P1.

Red: With an overlay file present, gate-start instruction construction still omits overlay guidance and emits no overlay snapshot event.

Green: Write `.supervisor/policy-overlay.yaml`, call `_workflow_gate_start_kwargs`, assert the instruction includes overlay guidance, the snapshot event records overlay hash/block hash/proposal id, and the returned gate-start payload carries those fields.

## test_policy_evolution_rejects_non_overlay_apply_target

Maps to: Slice B1, P2.

Red: AutoResearch policy proposals can target arbitrary prompt/config artifacts such as `prompts/outcome-review.md`.

Green: Proposal creation and approval both reject every target except `.supervisor/policy-overlay.yaml`, while the overlay target still produces an inert proposal requiring human approval.

## test_policy_rollback_rejects_non_overlay_target_pointer

Maps to: Slice B1, P2.

Red: A crafted rollback pointer can restore a non-overlay prompt/config path after operator approval.

Green: Rollback rejects any `target_path` except `.supervisor/policy-overlay.yaml`, emits no rollback event, and leaves the non-overlay file unchanged.

## test_policy_rollback_validates_all_targets_before_writing

Maps to: Slice B1, P2.

Red: A mixed rollback pointer with a valid overlay target followed by a non-overlay target writes the overlay file before rejecting the later forbidden target.

Green: Rollback prevalidates every target and backup before any write, rejects the mixed pointer, emits no rollback event, and leaves both the overlay and non-overlay file unchanged.

## test_quality_trends_rows_include_policy_overlay_hash_and_proposal_id

Maps to: Slice B2, P3.

Red: Quality trend rows persist acceptance metrics but no active policy version metadata.

Green: Seed a `supervisor_policy_overlay_snapshot`, record trends, and assert persisted rows plus query summaries include `policy_overlay_hash` and `policy_proposal_id`.

## test_policy_regression_drafts_one_rollback_and_does_not_apply

Maps to: Slice B2, P4.

Red: A degraded post-overlay trend window produces no rollback draft or mutates the overlay directly.

Green: Seed pre/post trend windows over threshold, run regression verification, assert one regression event and one rollback draft are recorded, and assert the overlay file remains unchanged.

## test_workflow_result_drafts_policy_regression_rollback_from_recorded_trends

Maps to: Slice B2, P4.

Red: Normal workflow completion records degraded post-overlay trend rows but never invokes rollback-draft regression verification.

Green: Seed an approved overlay proposal with a rollback pointer, complete a workflow with degraded post-overlay metrics, and assert `_workflow_result` records exactly one regression event and rollback draft without applying the rollback.

## test_near_duplicate_lessons_fold_and_no_benefit_lesson_retires

Maps to: Slice B3, P5.

Red: Lesson ids include source run id, so repeated lessons duplicate prompt guidance and never retire after ineffective injection.

Green: Normalized duplicate lessons fold into one row with increased observation count; after configured recurring feedback, the lesson is retained in history but excluded from `query_supervisor_lessons`.

## test_weekly_p11_audit_scheduler_writes_due_audit_row

Maps to: Slice B3, P5.

Red: P11 false-accept sampling only runs through direct manual calls and records no cadence state.

Green: The scheduler records a due P11 audit row once per cadence window, includes false-accept vocabulary, returns `observational_only=true` and `gate_authority=unchanged`, and leaves the `dual_agent_gate_result` event count unchanged.

## test_autoresearch_signal_generator_reads_reviewer_probe_and_lesson_signals

Maps to: Slice B3, P5.

Red: Folding repeated lessons into one row hides recurrence from AutoResearch draft generation.

Green: Three near-duplicate lesson observations fold to one lesson id but produce a draft with `provenance.signal_count == 3`, proving `observed_count` feeds the recurrence signal.

## Regression Set

- `tests/test_policy_overlay.py`
- `tests/test_autoresearch_policy_evolution.py`
- `tests/test_quality_trends.py`
- `tests/test_supervisor_lessons.py`
- `tests/test_autoresearch_generator.py`
- `tests/test_schema_migrations.py`
- `tests/test_postgres_ledger_lane.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
