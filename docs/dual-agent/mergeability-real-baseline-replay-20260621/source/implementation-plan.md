# Implementation Plan

## Boundary

Implement at `run_powered_factorial_mergeability_evaluation` first. Keep any helper private to mergeability measurement unless a second caller needs it.

## Steps

1. Add the absent-baseline boundary test and make `single_agent_baseline` unavailable when no explicit powered baseline row exists.
2. Add the valid produced-baseline row test and normalize baseline row fields into per-task results.
3. Add hash mismatch validation and gaming flag propagation.
4. Add replay-field validation so hash-matching rows that omit accept verdict, producer metadata, decision source, or prompt hash fail closed as malformed baseline evidence.
5. Add unavailable-row accounting coverage so unavailable baseline rows do not inflate rejected or false-reject counts.
6. Add legacy paired-calibration labeling without changing its old acceptance semantics.
7. Add report-only regression coverage and rerun the full mergeability test file.

## Files Expected

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `docs/dual-agent/mergeability-real-baseline-replay-20260621/`

## Files / Modules to Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `docs/dual-agent/mergeability-real-baseline-replay-20260621/`

## Risks

- Existing fixture calibration tests may assume the old metadata baseline; preserve that path and label it rather than deleting it.
- Baseline hash validation may accidentally compare against task hashes instead of candidate hashes; tests must corrupt only the candidate artifact hash.
- Unavailable baseline rows can distort matched-TAR reporting if they are counted as rejects; keep unavailable separate from accept and reject counts.
- Report-only guardrails must remain false even when baseline artifacts are valid.

## Traceability

- P1 -> `test_powered_factorial_requires_explicit_baseline_decisions`, `test_powered_factorial_consumes_replayable_baseline_decisions`
- P2 -> `test_powered_factorial_baseline_hash_mismatch_is_unavailable`, `test_powered_factorial_baseline_missing_replay_fields_is_unavailable`, `test_powered_factorial_unavailable_baseline_rows_do_not_count_as_rejects`
- P3 -> `test_powered_factorial_consumes_replayable_baseline_decisions`, `test_powered_factorial_baseline_hash_mismatch_is_unavailable`, `test_powered_factorial_baseline_missing_replay_fields_is_unavailable`
- P4 -> `test_legacy_metadata_baseline_is_labeled_not_real_baseline`, `test_real_baseline_reports_remain_report_only`
