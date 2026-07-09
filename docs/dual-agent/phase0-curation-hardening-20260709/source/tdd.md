# Phase 0 Curation Hardening TDD Plan

## Public Boundary

The primary public boundary is `scripts.swebench_pro_batch_driver` and its CLI-shaped `main` behavior. Tests may inject fake filesystem telemetry, fake subprocess output, and fake oracle callbacks below that boundary. Tests must not run live Docker, live oracle containers, live solver/model calls, or mutate the original preregistration.

## Test Cycles

### test_atomic_final_json_write_preserves_previous_artifact_on_mid_write_failure

Maps to: Slice 1, P1, P7

RED: Seed a valid final artifact, inject a failure after the temp file is written and before rename, and assert the final artifact still contains the original valid JSON.

GREEN: Implement same-filesystem atomic JSON writes for driver-final artifacts and route final artifact writes through it.

### test_atomic_final_json_write_never_leaves_zero_byte_artifact

Maps to: Slice 1, P1

RED: Inject a mid-write failure for a previously absent final artifact and assert the target path is absent or valid non-empty JSON, never zero bytes.

GREEN: Ensure temp cleanup and rename ordering prevents zero-byte target promotion.

### test_checkpoint_receipt_written_after_each_dry_oracle_instance

Maps to: Slice 2, P2

RED: Run curation over two records with a fake oracle and assert two checkpoint receipts exist with instance id, dry-oracle result, disk_free_gb, timestamp, image-cache telemetry, and hash verification.

GREEN: Add checkpoint receipt writing after each dry-oracle result.

### test_checkpoint_write_enospc_halts_with_blocked_execution_receipt

Maps to: Slice 2, P2, P4

RED: Inject an OSError representing ENOSPC during checkpoint write and assert a blocked-execution receipt is written, the driver exits nonzero, and no success roster is promoted.

GREEN: Catch checkpoint write failure, write the halt receipt atomically, and fail closed.

### test_resume_skips_verified_completed_checkpoints

Maps to: Slice 3, P3

RED: Seed a valid checkpoint for one instance, run curation with two records, and assert the fake oracle is not called for the completed instance while roster provenance reports one resumed checkpoint.

GREEN: Load and verify checkpoint receipts before curation and reuse completed entries.

### test_resume_reruns_tampered_checkpoint_receipt

Maps to: Slice 3, P3

RED: Seed a checkpoint whose payload hash no longer matches, rerun curation, and assert the fake oracle is called for that instance and provenance records an invalid checkpoint.

GREEN: Verify checkpoint hashes and treat mismatches as rerunnable, not resumable.

### test_disk_floor_breach_writes_halt_receipt_and_exits_nonzero

Maps to: Slice 4, P4

RED: Configure disk telemetry below the default floor before the first instance and assert `reason=disk_floor_reached`, nonzero exit, and no final roster success artifact.

GREEN: Add pre-instance disk-floor checks and blocked-execution halt receipt generation.

### test_disk_floor_above_threshold_allows_curation_to_proceed

Maps to: Slice 4, P4

RED: Configure disk telemetry above the floor and assert curation reaches the fake oracle and produces the expected roster.

GREEN: Wire disk telemetry into the curation loop without changing existing curation predicates.

### test_default_docker_prune_command_reclaims_images_and_records_reclaimed_bytes

Maps to: Slice 4, P5

RED: Assert the default prune command is image-level and fake image-cache telemetry produces a prune receipt with reclaimed bytes.

GREEN: Keep the command configurable, set the default to image-level prune, and record before/after image-cache sizes.

### test_container_only_prune_default_is_rejected_by_default_config

Maps to: Slice 4, P5

RED: Force a container-only default command shape and assert the helper classifies it as not image-reclaiming.

GREEN: Add default command validation while allowing explicit operator override with recorded command text.

### test_prereg_amendment_hashes_match_actual_files_and_original_prereg_unchanged

Maps to: Slice 5, P6, P7

RED: Read the original preregistration, amendment, batch driver, and any touched pinned script; assert original prereg SHA is the frozen pre-slice value and amendment old/new hashes match actual files.

GREEN: Add the append-only amendment after source changes land and update hash references.

### test_existing_batch_driver_thresholds_and_report_only_authority_remain_unchanged

Maps to: Slice 5, P7

RED: Extend existing manifest tests to assert statistical floors and all report-only authority flags remain unchanged after hardening.

GREEN: Keep curation logic, thresholds, spend gates, and authority flags untouched while adding robustness controls.

## Execution Order

1. RED/GREEN the atomic write helper before other file-emission changes.
2. RED/GREEN checkpoint receipt writing and checkpoint write failure handling.
3. RED/GREEN resume from verified checkpoints.
4. RED/GREEN disk-floor halt behavior and image-prune telemetry.
5. RED/GREEN prereg amendment validation after implementation hashes are known.
6. Run existing `tests/test_swebench_pro_batch_driver.py` plus any focused new tests.
