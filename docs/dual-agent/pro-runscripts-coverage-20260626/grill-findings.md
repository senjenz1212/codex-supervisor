# Grill Findings

Task id: `pro-runscripts-coverage-20260626`

## Finding 1: Helper-Only Coverage Would Miss Batch Runs

Verdict: revise, resolved.

The initial helper seam was necessary but not sufficient. The official replay
runner already knows `selected_instance_ids`; missing scripts must be checked
there before predictions, materialization, freeze, or oracle execution.

Resolution: wire `preflight_swe_bench_pro_run_scripts(...)` into
`swebench_mergeability_official_replay_runner(...)` when the Pro adapter or a
Pro scripts root is configured.

## Finding 2: `SWEBENCH_PRO_ORACLE_SCRIPTS_DIR` Must Be Authoritative

Verdict: revise, resolved.

If the operator sets `SWEBENCH_PRO_ORACLE_SCRIPTS_DIR`, the batch runner should
not silently rely on the three vendored directories. It should validate the
selected roster against the resolved root.

Resolution: the replay preflight triggers when the env var is set or an
explicit scripts dir is supplied, and the same resolved root is passed down to
adapter contexts.

## Finding 3: No Generic Script Fabrication

Verdict: pass with stronger proof, resolved.

The adapter already copies source `run_script.sh` and `parser.py`, but tests
needed to prove that missing scripts fail before Docker and that copied files
match the source scripts.

Resolution: add adapter-level missing-script and verbatim-copy tests, plus
receipt hashes for source scripts.

