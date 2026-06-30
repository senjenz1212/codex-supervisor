# TDD Plan

Task id: `pro-corpus-label-stability-20260629`

## Public Boundary

The first implementation proof is `repeat_oracle_labels(...)` plus `filter_stable_corpus(...)` in `scripts/swebench_pro_label_stability.py`, because that is the operator-facing importable seam that repeats oracle labels and filters a corpus. The CLI is a secondary boundary proving live work is explicit.

Live Docker, solver, Telegram, Anthropic, and OpenAI are faked below the boundary. Real oracle re-runs are execution evidence on the VM, not unit-test work.

## Cycle 1: Stable Candidate Kept

RED

Add `tests/test_swebench_pro_label_stability.py::test_stable_candidate_is_kept_with_original_label_and_context`. A fake oracle returns `(pass, pass)` three times. The candidate is kept unchanged and the fake sees the curated Pro context fields.

Minimal GREEN

Add `repeat_oracle_labels(...)`, `classify_stability(...)`, `filter_stable_corpus(...)`, and reuse the batch driver's context augmentation helper.

## Cycle 2: Disagreement Drops As Flaky

RED

Add `test_disagreeing_completed_repeats_are_dropped_as_unstable`. A fake oracle returns `(fail, pass)`, `(pass, pass)`, `(fail, pass)`.

Minimal GREEN

Classify completed but disagreeing pairs as `UNSTABLE`, drop them with `unstable_label`, and include them in the flake-rate numerator.

## Cycle 3: Unavailable Drops Fail-Closed

RED

Add `test_any_unavailable_repeat_is_dropped_fail_closed_not_flaky`.

Minimal GREEN

Classify any unavailable run as `UNAVAILABLE`, preserve the reason, and exclude it from the flake numerator.

## Cycle 4: Flake-Rate Math

RED

Add `test_flake_rate_uses_unstable_over_total_evaluated`.

Minimal GREEN

Compute `flake_rate = unstable / total_evaluated`.

## Cycle 5: No Relabeling Or Mutation

RED

Add `test_kept_candidates_are_not_relabelled_or_mutated`.

Minimal GREEN

Copy stable rows unchanged and never update `oracle_label` or patch hashes from repeat results.

## Cycle 6: Empty Output Fails Closed

RED

Add `test_empty_input_or_all_dropped_fails_closed`.

Minimal GREEN

Raise on empty input and all-dropped stable output.

## Cycle 7: CLI Live Opt-In

RED

Add `test_cli_without_allow_live_refuses_before_oracle_calls`.

Minimal GREEN

Parse CLI args and return code `2` before calling the default oracle unless `--allow-live` is present.

## Cycle 8: No Secret Values

RED

Add `test_outputs_do_not_include_secret_env_values`.

Minimal GREEN

Write reports without env values or patch text, and preserve only prediction rows in the stable JSONL.

## Refactor Check

- Keep the wrapper's interface small.
- Keep Pro context construction in one reused place.
- Keep live execution behind explicit CLI opt-in.
- Keep the gate report-only.
