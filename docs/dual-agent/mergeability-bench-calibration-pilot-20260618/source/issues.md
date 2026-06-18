# Issue Slices

## Slice 1: Corpus Manifest And Calibration

Priority: P1

Estimate: small

PRD promises: P1, P2.

Public boundary: `supervisor.mergeability_bench.build_mergeability_corpus_manifest` and `supervisor.mergeability_bench.validate_mergeability_corpus` loading `tests/fixtures/mergeability_bench/`.

Representative action: load the local corpus, grade every declared control, and produce a manifest plus calibration summary.

Scope: add manifest and calibration helpers, record task and candidate hashes, and reject ineligible aggregate tasks.

Acceptance Criteria:
- [ ] `test_mergeability_corpus_manifest_requires_positive_and_negative_controls` proves missing control sides are rejected.
- [ ] `test_mergeability_calibration_covers_seeded_failure_modes` proves required positive and negative controls grade correctly.
- [ ] No-op, known-bad, test-gaming, protected-path, and mutable-path controls fail deterministically.

Allowed outcomes: calibrated tasks include at least one passing positive control and one failing negative control; broken controls raise a deterministic validation error.

Forbidden outcomes: missing controls are silently included in aggregate reporting, no-op passes, known-bad passes, known-good fails, or control hashes are omitted.

## Slice 2: Paired Acceptance Pilot Report

Priority: P1

Estimate: medium

PRD promises: P3, P4.

Public boundary: `supervisor.mergeability_bench.run_paired_acceptance_pilot` over the manifest-backed candidate pool.

Representative action: compare a baseline self-reported or visible-test accept policy with Supervisor runtime-native blocker acceptance on the same candidates.

Scope: add fixed-pool paired arm reporting with false accepts, true accepts, false rejects, cost, duration, and disagreements.

Acceptance Criteria:
- [ ] `test_paired_acceptance_pilot_reports_baseline_false_accept_and_supervisor_rejection` proves seeded bad candidates distinguish the arms.
- [ ] `test_paired_acceptance_pilot_computes_true_accept_and_false_reject_rates` proves rates are computed from oracle labels.
- [ ] `test_paired_acceptance_pilot_uses_identical_candidate_pool_for_both_arms` proves arm comparison is paired.

Allowed outcomes: the report computes false_accept_rate, true_accept_rate, false_reject_rate, cost, duration, and disagreement rows for both arms while keeping report-only invariants false.

Forbidden outcomes: arms evaluate different candidate sets, a seeded bad candidate is counted as true accept, or the report mutates gate or policy state.

## Slice 3: Evidence Artifact Export

Priority: P2

Estimate: small

PRD promises: P1, P3, P4.

Public boundary: `run_paired_acceptance_pilot(..., output_dir=...)` writing evidence artifacts.

Representative action: run the local pilot and inspect the exported JSON and JSONL files.

Scope: write corpus manifest, calibration summary, paired report, and per-task row artifacts with runtime-native receipts.

Acceptance Criteria:
- [ ] `test_paired_acceptance_pilot_exports_replayable_artifacts` proves all requested files are written.
- [ ] Every exported row includes a runtime-native mergeability receipt.
- [ ] The paired report states `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

Allowed outcomes: artifact hashes and receipt ids are replayable, every evaluated candidate has a runtime-native mergeability receipt, and all exported reports state report-only invariants.

Forbidden outcomes: artifacts contain hand-entered scores without grader execution, omit receipts, or create an applyable AutoResearch policy proposal.
