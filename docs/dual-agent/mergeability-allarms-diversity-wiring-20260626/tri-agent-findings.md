# Tri-Agent Findings

## Agent A — Correct Reuse

Verdict: Pass. The implementation should import and call existing helpers instead of forking metric logic.

Key refs: `supervisor/swe_bench_mergeability.py:21`, `supervisor/swe_bench_mergeability.py:2967`, `supervisor/swe_bench_mergeability.py:2970`, `supervisor/mergeability_bench.py:4658`, `supervisor/mergeability_bench.py:4975`, `supervisor/mergeability_bench.py:5254`.

Folded changes: Keep implementation to imports, adapter input derivation, calls, and report insertion. Do not build the benchmark-to-policy bridge. Keep authority flags false.

## Agent B — Input Shape Match

Verdict: Pass with adapter requirement. Direct helper wiring is blocked because leave-one-out needs `reviewer_panel_results` keyed by candidate id, rows with `runtime_evidence_floor_accept`, and a `full_stack` summary.

Key refs: `supervisor/swe_bench_mergeability.py:2967`, `supervisor/swe_bench_mergeability.py:3209`, `supervisor/swe_bench_mergeability.py:3224`, `supervisor/mergeability_bench.py:4975`, `supervisor/mergeability_bench.py:4999`, `supervisor/mergeability_bench.py:5003`, `supervisor/mergeability_bench.py:5210`.

Folded changes: Add a thin adapter that derives `runtime_evidence_floor_accept` from official `s_probe_accept`, passes `s_full` as `full_stack`, filters verdict-present reviewer results for leave-one-out, uses unique official candidate keys to avoid collisions, and attaches the shared candidate-pool hash in all-arms assembly.

## Agent C — Honest Unavailability

Verdict: Pass after RED coverage repair. The first RED must cover no-reviewer and zero-oracle-grounded-reviewer-error paths at the public boundary.

Key refs: `supervisor/swe_bench_mergeability.py:2967`, `supervisor/swe_bench_mergeability.py:3059`, `supervisor/mergeability_bench.py:4986`, `supervisor/mergeability_bench.py:5259`, `supervisor/mergeability_bench.py:5314`.

Folded changes: The TDD plan and first RED include no-reviewer unavailable coverage plus `zero_oracle_grounded_reviewer_errors` coverage.

## Authority Confirmation

All authority flags remain false: `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced`.
