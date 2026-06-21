# TDD Grill Findings

## Finding 1: Helper-Only Risk

Status: resolved.

The plan starts at `swebench_mergeability_replay_runner` and CLI invocation rather than testing private manifest helpers first. Helper tests may be added only if behavior remains ambiguous after the public-boundary tests are green.

## Finding 2: Oracle Ordering Must Be Observable

Status: resolved.

The TDD plan requires disk artifacts for frozen decisions and oracle outputs so tests can verify decision artifacts are written before oracle labels are attached.

## Finding 3: Missing Panel Must Not Become Accept

Status: resolved.

The TDD plan includes unavailable-panel assertions and preserves the existing no-imputation rule for S_full.
