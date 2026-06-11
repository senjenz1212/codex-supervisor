# Implementation Plan: Policy Overlay Liveness

## Scope

Implement Phase B of the supervisor auto-evolution loop:

- Consume one whitelisted policy surface at `.supervisor/policy-overlay.yaml`.
- Inject the active overlay into lead instructions with replayable hashes.
- Restrict accepted AutoResearch policy proposals to that overlay surface.
- Attribute quality trend rows to the active overlay hash and proposal id.
- Draft rollback proposals on regression signals without applying them.
- Add lesson hygiene and weekly P11 audit scheduling.

## Public Boundaries

- `submit_dual_agent_workflow_job` / gate-start instruction construction consumes the overlay and records `supervisor_policy_overlay_snapshot`.
- `build_lead_prompt` includes overlay guidance before advisory lesson guidance.
- AutoResearch policy evolution accepts only `.supervisor/policy-overlay.yaml` targets.
- Quality trend metrics persist and query `policy_overlay_hash` and `policy_proposal_id`.
- Lesson query/injection excludes retired lessons and records repeated no-benefit feedback.

## Implementation Steps

1. Add `supervisor.policy_overlay` with deterministic YAML loading, hash computation, target-path normalization, and lead-instruction block rendering.
2. Thread policy overlay fields through MCP gate start, runner specs, lead invocation requests, handoff packets, and lead prompt rendering.
3. Update policy evolution proposal creation and apply/rollback validation so proposals cannot target arbitrary prompt/config files.
4. Extend SQLite/Postgres schemas and migrations for overlay attribution and lesson hygiene fields.
5. Add quality-trend attribution and a weekly P11 audit scheduler that records observations only.
6. Preserve two human touchpoints: operator activation for experiments and operator approval for policy apply.

## Validation

- `tests/test_policy_overlay.py` proves overlay injection and snapshot hashing.
- `tests/test_autoresearch_policy_evolution.py` proves policy proposals target only the overlay and do not auto-apply.
- `tests/test_quality_trends.py` proves trend rows carry overlay attribution and weekly P11 audit scheduling is read-only.
- `tests/test_supervisor_lessons.py` proves folded near-duplicate lessons, observation counts, and retirement of no-benefit recurring lessons.
- `tests/test_codex_supervisor_mcp_stdio.py` proves the MCP tool path uses the overlay target.
- Full pytest must stay green.

## Rollback

Rollback is source-only and data-compatible:

- Remove overlay injection from gate-start and lead-prompt paths.
- Revert proposal target validation to the prior policy-evolution behavior only if explicitly approved.
- Keep added nullable/defaulted schema columns harmless if data migration rollback is not required.

No automatic policy mutation is introduced. `default_change_allowed` remains false, and generated rollback proposals are drafts until operator approval.

## Files / Modules To Touch

- `supervisor/policy_overlay.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `supervisor/dual_agent_lead.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/autoresearch/policy_evolution.py`
- `supervisor/quality_trends.py`
- `supervisor/state.py`
- `supervisor/postgres_state.py`
- `supervisor/schema_migrations.py`
- `migrations/versions/20260610_0004_policy_overlay_trend_columns.py`
- `tests/test_policy_overlay.py`
- `tests/test_autoresearch_policy_evolution.py`
- `tests/test_quality_trends.py`
- `tests/test_supervisor_lessons.py`

## Risks

- Overlay guidance could look authoritative even though gates remain authoritative, so the prompt block must state that the overlay is advisory and cannot satisfy gates.
- Lesson folding could erase recurrence signal, so the folded row must retain observation count and AutoResearch must count repeated observations.
- Regression verification could become a hidden apply path, so tests must prove rollback proposals are drafts and overlay bytes are unchanged.

## Traceability

- P1 is covered by `test_applied_overlay_changes_next_gate_instruction_and_records_hash`.
- P2 is covered by `test_policy_evolution_rejects_non_overlay_apply_target`.
- P3 is covered by `test_quality_trends_rows_include_policy_overlay_hash_and_proposal_id`.
- P4 is covered by `test_policy_regression_drafts_one_rollback_and_does_not_apply`.
- P5 is covered by `test_near_duplicate_lessons_fold_and_no_benefit_lesson_retires` and `test_weekly_p11_audit_scheduler_writes_due_audit_row`.
