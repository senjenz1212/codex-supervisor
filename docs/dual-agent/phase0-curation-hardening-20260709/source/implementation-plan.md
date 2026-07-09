# Phase 0 Curation Hardening Implementation Plan

## Files / Modules To Touch

- `scripts/swebench_pro_batch_driver.py`: add same-filesystem atomic JSON writes, checkpoint receipt load/write/verify helpers, disk telemetry, disk-floor halt handling, image-prune telemetry, and CLI/config fields.
- `tests/test_swebench_pro_batch_driver.py`: add public-boundary regression tests named in `tdd.md` for atomic writes, checkpoints, resume behavior, disk-floor halts, image pruning, prereg amendment validation, and unchanged authority.
- `docs/dual-agent/pro-corpus-generate-label-20260626/artifacts/scale-prereg-20260629-amendment-1.json`: add the append-only preregistration amendment after implementation hashes are stable.
- `docs/dual-agent/phase0-curation-hardening-20260709/receipts/`: keep planning and validation receipts for the supervised flow.

## Execution Steps

1. Add RED tests for atomic final artifact writes, then implement the atomic write helper and route driver-final JSON artifacts through it.
2. Add RED tests for per-instance checkpoint receipts and checkpoint ENOSPC, then implement atomic checkpoint receipts and blocked-execution halt receipts.
3. Add RED tests for resume skip and tampered-checkpoint rerun, then implement checkpoint hash verification and roster provenance counts.
4. Add RED tests for disk-floor halt/proceed behavior and image-prune telemetry, then implement `disk_floor_gb`, disk/image telemetry, default image-level prune, and reclaimed-byte receipts.
5. Add the prereg amendment only after source hashes settle, then validate the amendment against actual file hashes and prove the original preregistration remains byte-identical.
6. Run the focused batch-driver test file, the relevant amendment validation test, and the requested no-mistakes validation loop before committing the implementation slice.

## Risks

- Resume logic can accidentally alter curation inclusion predicates if checkpoint payloads are not shaped exactly like freshly computed entries.
- Disk and Docker telemetry must be injectable for tests without weakening the live path or faking real oracle/Docker execution.
- Atomic write failures must leave no final zero-byte file and should clean temporary files without hiding the original exception.
- The prereg amendment must be generated after final source changes; writing it too early would create stale old/new SHA-256 evidence.

## Traceability

- P1 is covered by `test_atomic_final_json_write_preserves_previous_artifact_on_mid_write_failure` and `test_atomic_final_json_write_never_leaves_zero_byte_artifact`.
- P2 is covered by `test_checkpoint_receipt_written_after_each_dry_oracle_instance` and `test_checkpoint_write_enospc_halts_with_blocked_execution_receipt`.
- P3 is covered by `test_resume_skips_verified_completed_checkpoints` and `test_resume_reruns_tampered_checkpoint_receipt`.
- P4 is covered by `test_disk_floor_breach_writes_halt_receipt_and_exits_nonzero` and `test_disk_floor_above_threshold_allows_curation_to_proceed`.
- P5 is covered by `test_default_docker_prune_command_reclaims_images_and_records_reclaimed_bytes` and `test_container_only_prune_default_is_rejected_by_default_config`.
- P6 is covered by `test_prereg_amendment_hashes_match_actual_files_and_original_prereg_unchanged`.
- P7 is covered by `test_existing_batch_driver_thresholds_and_report_only_authority_remain_unchanged` plus the existing batch-driver manifest and solver-spend guard tests.
