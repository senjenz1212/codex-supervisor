# Implementation Plan: INTEGRITY-A Evidence Writes

## Files / Modules To Touch

- `supervisor/state.py`
- `supervisor/schema_migrations.py`
- `supervisor/policy_overlay.py`
- `supervisor/autoresearch/policy_evolution.py`
- `tests/test_integrity_a_evidence_writes.py`
- `tests/test_integrity_a_terminal_cas.py`
- `tests/test_integrity_a_audit_revisions.py`
- `tests/test_integrity_a_policy_symlinks.py`
- `tests/test_schema_migrations.py`
- `tests/test_autoresearch_policy_evolution.py`
- `docs/dual-agent/integrity-a-evidence-writes-20260711/prd.md`
- `docs/dual-agent/integrity-a-evidence-writes-20260711/tdd.md`

## Steps

1. Add a canonical duplicate terminal-completion RED test.
2. Guard completion with `terminal_outcome_json IS NULL` and a row-count check.
3. Add the conflicting completion RED test and discrepancy event payload.
4. Add immutable audit revision and legacy migration RED tests.
5. Create migration 11, append revisions, expose audit history, and update the latest projection atomically.
6. Add symlinked parent, target, and backup RED tests.
7. Centralize real-root containment, component validation, safe parent creation, and `O_NOFOLLOW` writes.
8. Route approval, rollback, and restoration through the safe writer.
9. Run focused existing workflow, quality-trend, schema, overlay, approval, and rollback tests.
10. Record exact commands and results without committing.

## Risks

- Raising before committing discrepancy evidence would hide the conflict; transaction ordering must be explicit.
- Audit timestamps measured in seconds can collide; the state method must allocate a monotonic value per run and gate.
- A mutable projection can drift from immutable revisions; audit history remains the source of truth and legacy state must be backfilled.
- A lexical containment check is insufficient against symlinks and races; directory file descriptors plus no-follow flags are required.
- Replacing `Path.write_bytes` changes the fault-injection seam; the post-write hash test must still corrupt the written target and verify restoration.
- Concurrent agents are editing unrelated watcher, runtime, replay, and claim-gate files; this slice must not rewrite or stage them.

## Traceability

- P1 -> `test_duplicate_identical_terminal_completion_is_an_idempotent_no_op`, `test_conflicting_terminal_completion_records_discrepancy_and_preserves_original`
- P2 -> `test_quality_trend_audits_append_revisions_and_project_the_latest`, `test_quality_trend_audit_migration_backfills_existing_projection`
- P3 -> `test_normalise_overlay_target_rejects_symlinked_parent_directory`, `test_policy_approval_rejects_symlinked_backup_directory_before_writing`, `test_policy_approval_rejects_symlinked_overlay_file_before_writing`
