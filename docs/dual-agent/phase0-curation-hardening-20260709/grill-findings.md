# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 1107333 `prd_review`: gate blocked
- event_id 1107608 `prd_review`: Low-severity: P5 forbidden outcome 'default prunes containers only' already satisfied at HEAD (docker_prune_command default docker image prune -af:677); net-new is reclaim/image-cache byte telemetry only (_run_prune:400-405 records no bytes) - TDD must pin telemetry not re-assert default; mitigated by grill Finding 4
- event_id 1107609 `prd_review`: both agents accepted
- event_id 1107635 `issues_review`: Low severity: Slice 4 AC3 (container-only default fails regression) exercises the already-green default `docker image prune -af`:677; net-new value is the not-image-reclaiming classifier helper, which is absent at HEAD, so vacuous-green risk is low and grill-findings Finding4 anticipates it.
- event_id 1107636 `issues_review`: both agents accepted
- event_id 1107671 `tdd_review`: Low-sev: test_default_docker_prune_command_reclaims couples an already-green assertion (default docker image prune -af at :677 is image-level) with a net-new RED assertion (reclaimed-bytes recording absent in _run_prune:400-405); not vacuous but green-lean
- event_id 1107671 `tdd_review`: Low-sev: test_existing_batch_driver_thresholds extends already-green test_batch_manifest_pins_thresholds_and_report_only_labels:204, carrying no RED (appropriate preservation guard)
- event_id 1107862 `tdd_review`: both agents accepted
- event_id 1107940 `implementation_plan`: low-sev: plan thin (no per-step parallel waves) but single_lead_worker execution layer makes linear TDD order the dependency graph
- event_id 1107940 `implementation_plan`: low-sev: P5 default prune docker image prune -af:677 already image-level (partly-green) but paired with net-new reclaim-byte telemetry and container-only classifier, not vacuous
- event_id 1108171 `implementation_plan`: both agents accepted
- event_id 1108325 `execution`: both agents accepted
- event_id 1108356 `outcome_review`: Low severity: test_prereg_amendment_hashes_match_actual_files_and_original_prereg_unchanged hardcodes new_sha256 (driver 43697a1a, test 68e2f8b7) and prereg 701d77177 asserting equality with current files; shasum blocked so content freshness could not be self-verified. Mitigation: all referenced files exist on disk (distinct from prior replay REVISE where files were absent); risk limited to hash drift which supervisor runtime floor detects by rerun.
- event_id 1108572 `outcome_review`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 1108574 `outcome_review`: Low severity: test_prereg_amendment_hashes_match_actual_files_and_original_prereg_unchanged hardcodes new_sha256 (driver 43697a1a, test 68e2f8b7) and prereg 701d77177 asserting equality with current files; shasum blocked so content freshness could not be self-verified. Mitigation: all referenced files exist on disk (distinct from prior replay REVISE where files were absent); risk limited to hash drift which supervisor runtime floor detects by rerun.
- event_id 1108587 `outcome_review`: Low severity: local shasum/python3 hashing and pytest are approval-blocked; amendment new_sha256 values (driver 43697a1a, test 68e2f8b7) could not be recomputed locally. Mitigated: the amendment test recomputes them at runtime and the supervisor floor reruns it, so any drift blocks the gate.
- event_id 1108587 `outcome_review`: Low severity: amendment file is untracked (git ?? ); correct as net-new per plan, and the test reads it from the working tree which the floor evaluates.
- event_id 1108590 `outcome_review`: runtime_evidence_failed: runtime_evidence_failed: failures=runtime_changed_files_missing_from_diff, runtime_deliverable_not_file
- event_id 1108592 `outcome_review`: Low severity: local shasum/python3 hashing and pytest are approval-blocked; amendment new_sha256 values (driver 43697a1a, test 68e2f8b7) could not be recomputed locally. Mitigated: the amendment test recomputes them at runtime and the supervisor floor reruns it, so any drift blocks the gate.
- event_id 1108592 `outcome_review`: Low severity: amendment file is untracked (git ?? ); correct as net-new per plan, and the test reads it from the working tree which the floor evaluates.
- event_id 1108612 `outcome_review`: Low-sev: amendment hardcodes new_sha256 values for driver/test/prereg; local hashing tools approval-blocked so freshness is verified transitively via the runtime test test_prereg_amendment_hashes_match_actual_files_and_original_prereg_unchanged (line 357 recomputes _sha256_file per changed file), which the supervisor runtime floor reruns.
- event_id 1108612 `outcome_review`: Low-sev: pytest not runnable locally (approval-blocked); no tests-passed claim made; runtime floor is the test authority.
- event_id 1108803 `outcome_review`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 1108805 `outcome_review`: Low-sev: amendment hardcodes new_sha256 values for driver/test/prereg; local hashing tools approval-blocked so freshness is verified transitively via the runtime test test_prereg_amendment_hashes_match_actual_files_and_original_prereg_unchanged (line 357 recomputes _sha256_file per changed file), which the supervisor runtime floor reruns.
- event_id 1108805 `outcome_review`: Low-sev: pytest not runnable locally (approval-blocked); no tests-passed claim made; runtime floor is the test authority.
- event_id 1108813 `outcome_review`: gate blocked
