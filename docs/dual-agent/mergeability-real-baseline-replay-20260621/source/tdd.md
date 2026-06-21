# TDD Plan

## Public Boundary

Use `run_powered_factorial_mergeability_evaluation` as the first public boundary. Tests may construct fixture candidates and arm decisions, but they must inspect the same arm summaries, per-task rows, gaming flags, and replay exports that operators use.

## Test Cases

### test_powered_factorial_requires_explicit_baseline_decisions

Maps to: ISS-1, P1
RED: Run powered factorial evaluation without `single_agent_baseline` decisions and assert the target invariant: every missing baseline row is unavailable, `accepted_count` is zero, and a baseline-evidence flag is present. The current implementation fails because it falls back to metadata acceptance.
GREEN: Require explicit baseline rows for the powered baseline arm; absent rows become unavailable and do not count as accepts.

### test_powered_factorial_consumes_replayable_baseline_decisions

Maps to: ISS-1, P1, P3
RED: Supply baseline rows with producer metadata, candidate artifact hashes, and accept/reject decisions, then assert producer metadata and hash-bound decision evidence appear in per-task rows and replay exports. The current implementation may consume accept/reject booleans, but it fails this test because it does not preserve produced-baseline evidence.
GREEN: Normalize the supplied rows and record the replayable baseline evidence beside each candidate result.

### test_powered_factorial_baseline_hash_mismatch_is_unavailable

Maps to: ISS-2, P2, P3
RED: Supply a baseline row whose candidate artifact hash does not match the compared candidate hash and assert the current report still treats the arm as accepted or rejected.
GREEN: Mark the row unavailable, add a baseline evidence gaming flag, and preserve the mismatch reason in the row.

### test_powered_factorial_baseline_missing_replay_fields_is_unavailable

Maps to: ISS-2, P2, P3
RED: Supply baseline rows that contain only a matching candidate artifact hash while omitting the explicit accept verdict, decision source, producer metadata, and prompt hash, then assert the current report treats those rows as available rejects.
GREEN: Treat hash-matching but replay-incomplete rows as malformed baseline evidence, mark them unavailable, keep them out of reject and false-reject accounting, and preserve the missing replay fields in the unavailable reason.

### test_powered_factorial_unavailable_baseline_rows_do_not_count_as_rejects

Maps to: ISS-2, P2
RED: Run powered factorial evaluation with missing or hash-mismatched baseline rows and assert the target accounting invariant: unavailable baseline rows increase unavailable count but do not increase rejected_count, false_reject_count, true_reject_count, or false_reject_denominator. The current summary path can count accept=false rows as rejects, so this catches denominator distortion directly.
GREEN: Keep unavailable baseline rows outside reject/false-reject accounting while preserving unavailable_count and gaming flags.

### test_legacy_metadata_baseline_is_labeled_not_real_baseline

Maps to: ISS-3, P4
RED: Run paired fixture calibration and assert the baseline is not distinguishable from a real produced baseline.
GREEN: Preserve the legacy behavior but label its decision source as metadata calibration, not produced single-agent baseline evidence.

### test_real_baseline_reports_remain_report_only

Maps to: ISS-1, ISS-2, ISS-3, P4
RED: Build a powered report with valid produced-baseline artifacts and assert the new baseline-evidence branch still cannot create an applyable proposal, default change, policy mutation, or gate advancement. Existing guardrails pass on older paths, but the new branch must prove it does not bypass them.
GREEN: Preserve report-only guardrails until powered live evidence and operator approval exist.

## RED/GREEN Plan

RED: Add one boundary test asserting missing baseline rows are unavailable and watching the current metadata fallback fail that target invariant.
GREEN: Change powered factorial baseline defaults to unavailable and require explicit rows.

RED: Add one boundary test for valid produced baseline rows that specifically requires producer metadata and hash-bound evidence, not just accept/reject consumption.
GREEN: Add baseline row normalization and report evidence fields.

RED: Add one boundary test for candidate hash mismatch.
GREEN: Add fail-closed validation and gaming flags.

RED: Add one boundary test for hash-matching rows missing replay evidence fields.
GREEN: Require replay fields before a baseline row becomes available.

RED: Add one boundary test proving unavailable baseline rows do not inflate reject or false-reject counts.
GREEN: Exclude unavailable baseline rows from reject accounting while retaining explicit unavailable counts.

RED: Add one boundary test for legacy calibration labeling.
GREEN: Add calibration-only labeling without changing old fixture behavior.

RED: Add one report-only policy derivation test against the new produced-baseline evidence branch.
GREEN: Keep metric and policy mutation guardrails false.
