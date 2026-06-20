# SWE-bench Mergeability Executable Runner TDD Plan

## Public Boundary And Seam

Public boundary: `swebench_mergeability_fixture_runner`.

Chosen seam/interface: a runner entrypoint that takes fixture metadata, a candidate artifact, configured public commands, configured oracle commands, an optional reviewer panel adapter, and an output directory. The seam emits a frozen decision artifact and calls `swebench_pro_mergeability_bridge_report` to produce `report.json`.

## Promise Coverage

P1 is covered by `test_fixture_runner_freezes_decisions_before_oracle_execution`.
P2 is covered by `test_fixture_runner_executes_public_probe_and_excludes_hidden_oracle`.
P3 is covered by `test_fixture_runner_marks_full_gate_unavailable_without_panel` and `test_fixture_runner_preserves_panel_disagreement_with_probe`.
P4 is covered by `test_fixture_runner_report_only_invariants_and_no_policy_outputs`.

## RED/GREEN Sequence

### test_fixture_runner_executes_public_probe_and_excludes_hidden_oracle

Maps to: ISS-1, ISS-2, P2

RED: Call the runner with a local fixture containing public source files, hidden oracle files, one public command, and one candidate patch. Assert the public command actually executes, stdout/stderr/status are recorded, and hidden files are absent from the public worktree before the decision artifact exists.

GREEN: Add fixture materialization, public worktree copy, candidate patch application, and public command execution behind the runner interface.

### test_fixture_runner_freezes_decisions_before_oracle_execution

Maps to: ISS-1, P1

RED: Call the runner and assert the frozen decision artifact exists with a hash and timestamp before oracle outcomes are read or attached. The test should fail if oracle output exists without a prior decision artifact.

GREEN: Write baseline, S_probe, and S_full decisions to disk before invoking hidden oracle commands, then pass frozen decisions to the bridge.

### test_fixture_runner_patch_apply_failure_is_recorded_not_crashed

Maps to: ISS-1, P2

RED: Provide a candidate patch that cannot apply and assert the runner records deterministic patch-apply failure in S_probe instead of crashing without a report.

GREEN: Catch patch-apply errors as public probe results and produce an unavailable or reject decision with command evidence.

### test_fixture_runner_marks_full_gate_unavailable_without_panel

Maps to: ISS-3, P3

RED: Call the runner without a reviewer panel and assert S_full status is unavailable with `reviewer_panel_unavailable`, not accepted or copied from S_probe.

GREEN: Add reviewer-panel dependency injection and explicit unavailable handling for missing panel configuration.

### test_fixture_runner_preserves_panel_disagreement_with_probe

Maps to: ISS-3, P3

RED: Inject a reviewer panel adapter that disagrees with S_probe and assert the disagreement appears in row data, summary data, reviewer packet refs, and independent reviewer results.

GREEN: Build public-only reviewer packets, run the adapter after public probe evidence is ready, and preserve the returned panel decision in bridge arm decisions.

### test_fixture_runner_report_only_invariants_and_no_policy_outputs

Maps to: ISS-4, P4

RED: Run a fixture with positive and negative oracle outcomes and assert non-empty FAR/TAR denominators, false report-only flags, and absence of policy proposal artifacts.

GREEN: Attach deterministic local oracle outcomes after decision freeze, emit `report.json`, and preserve all report-only invariants from the bridge.

## Regression Tests

Run `pytest tests/test_swe_bench_pro_mergeability_bridge.py tests/test_swe_bench_pro_eval.py tests/test_mergeability_bench.py` after the new fixture-runner tests. Existing pass-at-k behavior, bridge report construction, oracle isolation, and mergeability bench behavior must remain green.

## Translation Audit

Every PRD promise has a named issue and a boundary-level test. The first RED test crosses the runner public boundary and observes real public command execution rather than a private helper. Helper assertions are allowed only after the runner has emitted observable worktree, decision, reviewer, oracle, and report artifacts.
