# PRD Grill Findings

Task id: `pro-corpus-label-stability-20260629`

## Finding 1: The wrapper must not become a relabeler.

Status: resolved.

Resolution: P1 forbids relabeling and the tests assert kept rows retain their original `oracle_label` and hashes even when the repeated oracle runs would imply a different label.

## Finding 2: Pro oracle context must not drift from the corpus builder.

Status: resolved.

Resolution: The implementation imports the batch driver's context augmentation helper instead of re-deriving `base_commit`, `repo`, `dockerhub_tag`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `selected_test_files_to_run`, `before_repo_set_cmd`, and `swe_bench_pro_scripts_dir`.

## Finding 3: All-dropped output is easy to misread as success.

Status: resolved.

Resolution: P2 and the tests require empty input and all-dropped outputs to fail closed; the CLI does not write an empty stable corpus as a successful artifact.

## Finding 4: Live re-runs can accidentally spend Docker budget.

Status: resolved.

Resolution: P3 requires `--allow-live`; the CLI exits with code `2` before reading or invoking the oracle when the flag is absent.
