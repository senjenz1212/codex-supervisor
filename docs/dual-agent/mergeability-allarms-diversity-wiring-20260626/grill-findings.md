# PRD Grill Findings

## Gate Status

Resolved. `prd_review` accepted in `ledger.jsonl`.

## Findings

### G1 — Leave-one-out needs the official row shape made explicit

File refs: `supervisor/swe_bench_mergeability.py:2967`, `supervisor/swe_bench_mergeability.py:3194`, `supervisor/mergeability_bench.py:4975`.

Finding: `_leave_one_reviewer_out_analysis` expects reviewer panel results keyed by candidate id and a public evidence floor on each row. The PRD must not hand-wave that shape, because `_official_reviewer_analysis_rows` exposes `supervisor_full_gate_reviewer_results` and oracle labels but not an explicitly named factorial floor.

Resolution: Keep the public boundary at `_build_official_all_arms_diagnostic_report`, derive the minimal adapter inputs inside official report assembly, and reuse `_leave_one_reviewer_out_analysis` unchanged.

### G2 — Report-only authority must remain visibly false

File refs: `supervisor/swe_bench_mergeability.py:3008`, `supervisor/swe_bench_mergeability.py:3066`, `supervisor/swe_bench_mergeability.py:3070`.

Finding: Surfacing independence metrics could be mistaken for benchmark-to-policy promotion evidence unless the PRD explicitly preserves the report-only boundary.

Resolution: P1 forbids the autonomous benchmark-to-policy bridge and requires all authority flags to remain false.

### G3 — Empty reviewer rows must not become positive independence evidence

File refs: `supervisor/mergeability_bench.py:4986`, `supervisor/mergeability_bench.py:5265`, `supervisor/mergeability_bench.py:5271`.

Finding: The PRD needs to preserve the existing unavailable states for no reviewers, one reviewer, and zero oracle-grounded reviewer errors.

Resolution: P1 allowed outcomes now require honest unavailable statuses for empty reviewer rows or zero oracle-grounded reviewer errors.
