# Official SWE-bench Verified Replay Smoke

This directory records the Slice 3 smoke run for `sympy__sympy-14711` on
`SWE-bench/SWE-bench_Verified`. The run uses a single gold patch as a
known-resolved plumbing input so the official Docker harness path can be
proved end to end. It is not supervisor-quality evidence, not powered
measurement evidence, and not human-mergeability evidence.

The compact receipt is `artifact-index.json`. The key durable outputs are:

- `replay-output/official_replay_report.json`
- `replay-output/official_replay_manifest.json`
- `replay-output/replay/instances/sympy__sympy-14711/frozen_decisions.json`
- `replay-output/replay/instances/sympy__sympy-14711/oracle_outputs.json`
- `oracle-artifacts/sympy__sympy-14711/gold-official-smoke/logs/run_evaluation/supervisor-official-replay-smoke-20260622-sympy__sympy-14711-gold-official-smoke/supervisor-replay/sympy__sympy-14711/report.json`

The full public checkout and copied candidate worktree are intentionally
ignored from git because together they are hundreds of megabytes. Their paths
remain in the local report artifacts for reviewer inspection during this run,
while the committed index preserves the report hashes and the official harness
receipt.
