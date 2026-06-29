# TDD Grill Findings

## Gate Status

Resolved. `tdd_review` accepted in `ledger.jsonl`.

## Findings

### T1 — First RED must hit the returned diagnostic dict

File refs: `supervisor/swe_bench_mergeability.py:2848`, `supervisor/swe_bench_mergeability.py:2996`.

Finding: A helper-only test would not prove the all-arms public report contract.

Resolution: The first RED calls `_build_official_all_arms_diagnostic_report` and asserts on the returned dict.

### T2 — The unavailable cases must be in the first cycle

File refs: `supervisor/mergeability_bench.py:4986`, `supervisor/mergeability_bench.py:5265`, `supervisor/mergeability_bench.py:5271`.

Finding: Only testing the populated case could silently turn missing or zero-error evidence into a misleading available metric.

Resolution: The first RED includes no-reviewer and zero-oracle-grounded-error official report fixtures and asserts unavailable statuses.

### T3 — Metric logic must stay delegated

File refs: `supervisor/mergeability_bench.py:4658`, `supervisor/mergeability_bench.py:4975`, `supervisor/mergeability_bench.py:5254`.

Finding: The green implementation must not copy or reinterpret the three existing algorithms.

Resolution: The TDD plan limits implementation to imports, adapter inputs, calls, and report insertion.
