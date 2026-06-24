# Issues

## Slice 1: Run Fixture Full-Panel Measurement With Bounded Parallelism

Type: AFK
Priority: P0
Estimate: M
Scope: Add a public fixture measurement path that runs configured full-panel mergeability reviews with explicit candidate and reviewer worker limits while preserving deterministic aggregate ordering. The slice keeps existing public probe and full-gate semantics intact, but reduces all-corpus wall-clock risk by allowing bounded parallel work.
PRD promise: P1, P3, P4
First public-boundary RED test: `test_full_corpus_runner_executes_fake_configured_reviewers_with_bounded_parallelism`

Acceptance Criteria:
- [ ] The runner accepts conservative worker-limit options for candidates and reviewers.
- [ ] Fake configured reviewers can prove multiple candidates run under the configured candidate bound.
- [ ] `max_wall_clock_s` stops new candidate scheduling and writes a checkpointed partial report with a resume command.
- [ ] Candidate selector and `max_candidates` options run only the selected diagnostic subset and cannot be labeled as a full-corpus run.
- [ ] Aggregate rows remain sorted deterministically by task and candidate identity.
- [ ] Timeout, missing verdict, or partial roster is reported as unavailable and never accepted.
- [ ] S_probe, S_full, panel marginal status, discordance, per-reviewer arms, and inter-reviewer agreement remain in the aggregate report.

## Slice 2: Checkpoint Public-Safe Per-Candidate Results

Type: AFK
Priority: P0
Estimate: M
Scope: Persist public-safe per-candidate reviewer results before aggregate report assembly, and reuse them only when candidate hash, reviewer packet hash, reviewer roster identity, option metadata, and schema version match. Stale, malformed, or leak-positive checkpoints are recomputed or rejected.
PRD promise: P2, P3, P4
First public-boundary RED test: `test_checkpoint_resume_reuses_matching_candidate_packet_and_recomputes_stale_checkpoint`

Acceptance Criteria:
- [ ] Each completed candidate writes a checkpoint before aggregate report assembly.
- [ ] Matching checkpoints are reused and recorded as reused evidence.
- [ ] Stale or malformed checkpoints are recomputed and recorded as recomputed evidence.
- [ ] Timeout and partial-roster checkpoint rows remain unavailable and never accepted.
- [ ] Checkpoint payloads pass the existing oracle leak detector.

## Slice 3: Render Public-Only Annotation Dashboard

Type: AFK
Priority: P1
Estimate: S
Scope: Render an annotation-ready HTML dashboard from the public aggregate report and checkpoint metadata. The dashboard exposes candidate counts, n_good, n_bad, S_probe and S_full rates, panel marginal status, discordance, per-reviewer arms, inter-reviewer agreement, unavailable reasons, and report-only flags for Lavish review without scoring authority.
PRD promise: P4, P5
First public-boundary RED test: `test_annotation_dashboard_renders_public_metrics_without_oracle_material`

Acceptance Criteria:
- [ ] The dashboard renders from public report data and checkpoint metadata only.
- [ ] The dashboard includes panel marginal status, discordance count, per-reviewer arms, and unavailable reasons.
- [ ] Discordance is reported even when matched-TAR panel marginal is not computable.
- [ ] Completed, partial, and diagnostic reports keep metric_applyable, improvement_claim_allowed, policy_mutated, and gate_advanced false.
- [ ] The dashboard excludes hidden tests, final_score, oracle_accept, expected_outcome, protected path content, and hidden oracle markers.
- [ ] The dashboard can be opened with `npx -y lavish-axi <html-file>` without installing hooks.
- [ ] Dashboard generation does not alter scoring, policy, or gate authority.
