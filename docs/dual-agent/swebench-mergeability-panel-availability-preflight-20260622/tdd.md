# TDD Plan

## Boundary First

The first RED tests exercise `swebench_mergeability_replay_runner` because the user-visible risk is the emitted S_full report, not the private panel helper shape. Helper behavior is only trusted after the replay report proves the right distinction.

### test_replay_runner_unconfigured_panel_preflight_reports_uninvoked

Maps to: Slice 1, P1, P4, P5

RED: Run replay without a reviewer panel and assert the configured-panel preflight reports uninvoked and S_full stays unavailable.

GREEN: Add a preflight summary builder and attach it to fixture and replay reports.

### test_replay_runner_accepts_configured_style_panel_result

Maps to: Slice 1, Slice 2, P3, P4

RED: Run replay with a configured-style accepting full roster and assert full_roster_available=true with available reviewer evidence.

GREEN: Preserve configured-style panel metadata through strict normalization and aggregate it in preflight output.

### test_replay_runner_configured_missing_reviewer_keeps_s_full_unavailable

Maps to: Slice 2, P2, P4, P5

RED: Run replay with one missing configured reviewer verdict and assert S_full is unavailable, missing_reviewer_verdict is reported, and transcript/output hashes are preserved.

GREEN: Treat missing configured roster members as unavailable before S_full quality decisions.

### test_replay_runner_configured_missing_roster_evidence_keeps_s_full_unavailable

Maps to: Slice 2, P2, P4, P5

RED: Run replay with configured mode and an accepting panel result that omits reviewer_ids, then assert S_full remains unavailable and missing_reviewer_roster is reported.

GREEN: Require explicit configured roster evidence before full_roster_available can become true.

### test_replay_runner_configured_quality_reject_is_not_infrastructure_unavailable

Maps to: Slice 2, P3, P4

RED: Run replay with a full roster where one reviewer rejects and assert S_full rejects while unavailable=false and the preflight classification is quality_reject.

GREEN: Classify full-roster revise or deny as quality rejection rather than infrastructure failure.
