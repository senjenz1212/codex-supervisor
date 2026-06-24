# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `supervisor/swe_bench_mergeability.py`
- `tests/test_mergeability_bench.py`
- `tests/test_swe_bench_pro_mergeability_bridge.py`
- `docs/dual-agent/mergeability-bounded-parallel-panel-runner-20260623/source/implementation-plan.md`

## Risks

- Bounded parallelism could make aggregate ordering nondeterministic if completed rows are emitted directly instead of sorted by task id and candidate id.
- Checkpoint reuse could trust stale or malformed evidence unless identity covers candidate hash, reviewer packet hash, roster identity, option metadata, and schema version.
- Timeout and partial reviewer roster handling could accidentally become acceptance unless unavailable rows are excluded from accepted S_full decisions.
- The HTML dashboard could leak hidden oracle terms or become confused with scoring unless it is built only from public report fields and kept outside metric computation.

## Traceability

- P1 -> test_full_corpus_runner_executes_fake_configured_reviewers_with_bounded_parallelism
- P1 -> test_max_candidate_workers_limits_concurrent_candidate_execution
- P1 -> test_max_reviewer_workers_limits_concurrent_reviewers_per_candidate
- P1 -> test_max_wall_clock_s_writes_partial_report_and_resume_command
- P1 -> test_candidate_selector_and_max_candidates_limit_diagnostic_run_without_full_corpus_claim
- P2 -> test_checkpoint_written_before_aggregate_report
- P2 -> test_checkpoint_resume_reuses_matching_candidate_packet_and_recomputes_stale_checkpoint
- P2 -> test_max_wall_clock_s_writes_partial_report_and_resume_command
- P3 -> test_timeout_missing_verdict_and_partial_roster_are_unavailable_never_accept
- P3 -> test_max_wall_clock_s_writes_partial_report_and_resume_command
- P4 -> test_full_fixture_corpus_smoke_writes_report_checkpoints_runtime_evidence_and_html
- P4 -> test_candidate_selector_and_max_candidates_limit_diagnostic_run_without_full_corpus_claim
- P4 -> test_panel_marginal_not_computed_still_reports_probe_full_discordance
- P4 -> test_report_only_invariants_false_for_all_checkpointed_runs
- P5 -> test_checkpoint_and_dashboard_leak_detection_blocks_hidden_oracle_material
- P5 -> test_annotation_dashboard_renders_public_metrics_without_oracle_material

## Steps

1. Add public runner/options plumbing that routes current paired acceptance behavior through worker-limit and checkpoint options.
2. Add candidate worker scheduling and deterministic row sorting.
3. Add configured reviewer fanout bounding or explicit serial fanout reporting.
4. Convert timeout, exception, missing verdict, and partial roster into unavailable evidence that never accepts.
5. Add per-candidate checkpoint write, exact identity validation, matching reuse, and stale recomputation.
6. Reuse oracle leak detection for checkpoints, aggregate report data, and dashboard data.
7. Render the public-only annotation dashboard.
8. Run focused tests and then the real configured-panel fixture corpus command, producing complete or checkpointed partial runtime evidence.
