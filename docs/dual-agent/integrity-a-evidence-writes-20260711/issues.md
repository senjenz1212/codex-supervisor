# Issues: INTEGRITY-A Evidence Writes

## Slice 1: Terminal completion compare-and-set

Scope: Make the first terminal outcome immutable, treat canonical duplicates as no-ops, and append discrepancy evidence for conflicts before failing closed.

PRD promise: P1

Public seam: `State.complete_dual_agent_workflow_job`.

Acceptance Criteria:

- [ ] `test_duplicate_identical_terminal_completion_is_an_idempotent_no_op` proves one terminal event and no second mutation.
- [ ] `test_conflicting_terminal_completion_records_discrepancy_and_preserves_original` proves both outcomes are recorded while the original job row remains intact.
- [ ] Existing terminal persistence, redaction, and transaction rollback tests remain green.

Priority: P0

## Slice 2: Immutable quality-trend audit revisions

Scope: Append audit revisions, expose them through the state interface, keep the latest projection compatible, and backfill existing audit data.

PRD promise: P2

Public seam: `State.update_quality_trend_audit`, `State.list_quality_trend_audits`, and forward migrations.

Acceptance Criteria:

- [ ] `test_quality_trend_audits_append_revisions_and_project_the_latest` proves two revisions survive and the latest projection is returned.
- [ ] `test_quality_trend_audit_migration_backfills_existing_projection` proves legacy audit evidence enters immutable history.
- [ ] Schema migration tests verify the composite primary key and migration idempotency.

Priority: P0

## Slice 3: Symlink-safe policy and backup writes

Scope: Reject symlinked overlay targets and rollback paths while preserving legitimate approval, rollback, and post-write hash checks.

PRD promise: P3

Public seam: `normalise_overlay_target`, `approve_policy_proposal`, and `rollback_policy_proposal`.

Acceptance Criteria:

- [ ] `test_normalise_overlay_target_rejects_symlinked_parent_directory` blocks a parent escape.
- [ ] `test_policy_approval_rejects_symlinked_backup_directory_before_writing` blocks backup escape before mutation.
- [ ] `test_policy_approval_rejects_symlinked_overlay_file_before_writing` blocks a final-file escape.
- [ ] Existing approval and rollback regression tests prove legitimate in-repository writes still succeed.

Priority: P0
