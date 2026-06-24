# TDD Plan

## Public Boundary

The first RED tests call the public fixture mergeability measurement seam that produces a paired acceptance report from a bench root, configured reviewer panel options, worker limits, checkpoint options, and an output directory. Fake reviewers may sit below the configured reviewer adapter seam, but the tests must not call private scheduler helpers as the proof for any PRD promise.

## Test Cases

### test_full_corpus_runner_executes_fake_configured_reviewers_with_bounded_parallelism

Maps to: Slice 1, P1, P3, P4
RED: Call the public fixture runner with fake configured reviewers over all fixture candidates and assert deterministic task/candidate ordering, worker metadata, S_probe/S_full report fields, and report-only flags.
GREEN: Add the smallest public runner/options layer that delegates to existing paired acceptance calculation and sorts completed rows before aggregation.

### test_max_candidate_workers_limits_concurrent_candidate_execution

Maps to: Slice 1, P1
RED: Use slow fake candidate reviews and assert observed concurrent candidate execution never exceeds `max_candidate_workers`.
GREEN: Add bounded candidate scheduling behind the public runner without changing report semantics.

### test_max_reviewer_workers_limits_concurrent_reviewers_per_candidate

Maps to: Slice 1, P1, P3
RED: Use slow fake reviewer calls and assert observed per-candidate reviewer fanout never exceeds `max_reviewer_workers`, or assert the report records reviewer fanout as serial when explicitly deferred.
GREEN: Add bounded reviewer fanout inside the configured panel path, or expose serial reviewer fanout as a deliberate recorded limitation.

### test_timeout_missing_verdict_and_partial_roster_are_unavailable_never_accept

Maps to: Slice 1, Slice 2, P3, P4
RED: Make one reviewer time out, one raise, and one return no verdict; assert per-reviewer evidence remains but S_full is unavailable and false-accept accounting never treats the row as accepted.
GREEN: Wrap configured reviewer calls with timeout and fail-closed unavailable conversion.

### test_max_wall_clock_s_writes_partial_report_and_resume_command

Maps to: Slice 1, Slice 2, P1, P2, P3, P4
RED: Call the public fixture runner with slow fake reviewers and a low `max_wall_clock_s`; assert it stops before starting new candidates, writes completed per-candidate checkpoints, writes a partial aggregate marked not trusted, records interrupted/unavailable counts, and includes the exact resume command.
GREEN: Add runner-level wall-clock accounting around candidate scheduling so a wall-clock stop is unavailable/partial evidence and never an accepted candidate.

### test_candidate_selector_and_max_candidates_limit_diagnostic_run_without_full_corpus_claim

Maps to: Slice 1, P1, P4
RED: Call the public runner with an explicit candidate selector and with `max_candidates`; assert only selected candidates run, output ordering remains deterministic, diagnostic metadata records the selector, and the report refuses to label the run as the full 21-candidate corpus.
GREEN: Add candidate selection/max-candidate options before scheduling and include diagnostic-scope metadata in the report.

### test_checkpoint_written_before_aggregate_report

Maps to: Slice 2, P2
RED: Run a candidate with checkpointing enabled and assert a public-safe per-candidate JSON checkpoint exists before the aggregate report is written.
GREEN: Write candidate checkpoints immediately after candidate measurement completes and before aggregate assembly.

### test_checkpoint_resume_reuses_matching_candidate_packet_and_recomputes_stale_checkpoint

Maps to: Slice 2, P2, P3
RED: Run the same candidate twice with matching identity and assert the second run invokes no reviewer; then mutate the checkpoint identity and assert the runner recomputes the candidate.
GREEN: Add exact checkpoint identity validation using candidate hash, reviewer packet hash, roster identity, option metadata, and schema version.

### test_checkpoint_and_dashboard_leak_detection_blocks_hidden_oracle_material

Maps to: Slice 2, Slice 3, P2, P5
RED: Inject hidden oracle markers into a synthetic checkpoint and synthetic report-to-HTML input; assert leak detection fails before writing or publishing public artifacts.
GREEN: Reuse the existing oracle leak detector for checkpoint and dashboard payloads.

### test_annotation_dashboard_renders_public_metrics_without_oracle_material

Maps to: Slice 3, P4, P5
RED: Render a dashboard from a representative public report and assert candidate counts, n_good, n_bad, S_probe/S_full FAR/TAR, panel marginal status, discordance count, per-reviewer arms, agreement, unavailable reasons, and report-only flags appear while forbidden oracle keys are absent.
GREEN: Add the public-only HTML renderer and keep Lavish as a viewer-only workflow.

### test_panel_marginal_not_computed_still_reports_probe_full_discordance

Maps to: Slice 1, Slice 3, P4
RED: Build a report where S_full is available but matched-TAR panel marginal is not computable; assert the report still records S_probe-vs-S_full discordant counts, per-candidate disagreement rows, and the reason the marginal is unavailable.
GREEN: Keep discordance accounting independent from matched-TAR marginal computation.

### test_report_only_invariants_false_for_all_checkpointed_runs

Maps to: Slice 1, Slice 2, Slice 3, P4
RED: Generate completed, partial, and diagnostic reports and assert each has `metric_applyable=false`, `improvement_claim_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.
GREEN: Centralize report-only invariant stamping for all bounded runner report paths.

### test_full_fixture_corpus_smoke_writes_report_checkpoints_runtime_evidence_and_html

Maps to: Slice 1, Slice 2, Slice 3, P1, P2, P4, P5
RED: Run the full fixture corpus with fake reviewers and assert aggregate report, per-candidate checkpoints, runtime evidence, and HTML dashboard are produced.
GREEN: Wire the command or public runner path used for the real configured-panel calibration.

## RED/GREEN Plan

RED: Start with `test_full_corpus_runner_executes_fake_configured_reviewers_with_bounded_parallelism` at the public runner seam.
GREEN: Add only enough runner/options plumbing to reuse current paired acceptance report generation.

RED: Add `test_max_candidate_workers_limits_concurrent_candidate_execution`.
GREEN: Add bounded candidate scheduling and deterministic row sorting.

RED: Add `test_max_reviewer_workers_limits_concurrent_reviewers_per_candidate`.
GREEN: Add bounded reviewer fanout or explicit serial fanout reporting.

RED: Add fail-closed timeout and partial-roster tests.
GREEN: Convert infrastructure failures into unavailable evidence that never accepts.

RED: Add wall-clock and diagnostic selection tests.
GREEN: Add runner-level `max_wall_clock_s`, candidate selector, and `max_candidates` handling before candidate scheduling and report full-corpus-vs-diagnostic scope honestly.

RED: Add checkpoint write, resume, stale recompute, and leak-detection tests.
GREEN: Add checkpoint identity validation and safe artifact writes.

RED: Add dashboard rendering, discordance-without-marginal, report-only invariant, and full fixture smoke tests.
GREEN: Add the public-only HTML renderer, independent discordance accounting, invariant stamping, and runtime evidence export.

## Test Doubles

Fake reviewers are allowed only below the configured reviewer adapter seam. They may simulate accept, reject, revise, timeout, exception, missing verdict, and slow execution. The public runner, report assembly, checkpoint validation, leak detection, and dashboard rendering use real code paths.

## Acceptance Evidence

Acceptance requires the PRD-to-TDD artifact chain, public-boundary tests for each promise, existing mergeability regression tests, runtime evidence for the real configured-panel fixture run or checkpointed partial run, and a triagent reviewer packet containing changed files, declared tests, executed tests, checkpoint samples, aggregate report, HTML path, and oracle-isolation proof.
