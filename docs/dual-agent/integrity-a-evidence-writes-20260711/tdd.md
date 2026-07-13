# TDD Plan: INTEGRITY-A Evidence Writes

## Public Boundary

Tests call the same interfaces used by detached workers and policy operators: terminal completion on `State`, audit update/query on `State`, overlay normalization, policy approval, and rollback. Filesystem helpers are verified through policy evolution rather than as substitute helper-only tests.

## Test Cases

### test_duplicate_identical_terminal_completion_is_an_idempotent_no_op

Maps to: P1

RED: a canonical duplicate overwrites the row and appends a second terminal event.

GREEN: compare existing canonical JSON, commit no changes, and return without a new event.

### test_conflicting_terminal_completion_records_discrepancy_and_preserves_original

Maps to: P1

RED: a different second completion overwrites the original or fails without evidence.

GREEN: append one discrepancy event containing original and conflicting outcomes, commit it, preserve the job row, and raise.

### test_quality_trend_audits_append_revisions_and_project_the_latest

Maps to: P2

RED: the second audit mutates the only stored row and destroys the first revision.

GREEN: insert each audit under a monotonic composite key and update the rebuildable projection from the latest revision.

### test_quality_trend_audit_migration_backfills_existing_projection

Maps to: P2

RED: an upgraded legacy database has an empty immutable audit table despite populated P11 projection fields.

GREEN: migration 11 copies non-empty legacy audit metrics and details with the recorded computed time.

### test_normalise_overlay_target_rejects_symlinked_parent_directory

Maps to: P3

RED: the whitelisted relative string passes when `.supervisor` is a symlink outside the repository.

GREEN: reject any symlink component and any real path outside the real repository root.

### test_policy_approval_rejects_symlinked_backup_directory_before_writing

Maps to: P3

RED: approval follows a symlinked rollback directory and writes the backup outside the repository.

GREEN: validate and open backup parents relative to the repository root with no-follow semantics before any write.

### test_policy_approval_rejects_symlinked_overlay_file_before_writing

Maps to: P3

RED: approval truncates an external file through a symlinked overlay path.

GREEN: reject the final symlink before backup creation, target mutation, or approval event emission.

## RED/GREEN Plan

RED: add one public-interface regression for canonical duplicate terminal completion.
GREEN: add the minimal existing-outcome branch and guarded compare-and-set.

RED: add the conflicting completion regression.
GREEN: persist discrepancy evidence, preserve the original, then fail closed.

RED: add two-revision audit coverage.
GREEN: add the immutable table, append path, projection, and list interface.

RED: add legacy migration coverage.
GREEN: backfill existing audit projection data.

RED: add symlink normalization and approval regressions.
GREEN: centralize repository-local validation and no-follow writes, then rerun legitimate approval/rollback tests.
