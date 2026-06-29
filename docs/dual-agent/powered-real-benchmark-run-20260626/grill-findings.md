# PRD Grill Findings

Task id: `powered-real-benchmark-run-20260626`

## Verdict

Accepted after tightening. The initial intent said "run the powered real benchmark", but current code and artifacts prove the scaled corpus is absent. The PRD therefore keeps the target DoD intact and records the present run as blocked instead of pretending the benchmark is complete.

## Findings

### G1. Raw sample size is not power.

Finding: `>=30` oracle-good and `>=30` oracle-bad are necessary but insufficient. `run_powered_factorial_mergeability_evaluation(...)` documents that `metric_applyable` requires both sample-size sufficiency and paired-power sufficiency (`supervisor/mergeability_bench.py:1812`).

Resolution: P1 requires `paired_power.status="sufficient"` and the `full_supervisor_stack` McNemar comparison.

### G2. The all-arms diagnostic and powered report carry different labels.

Finding: the powered runner adds `evidence_conversion_power_contract` but the held-out proxy `benchmark_oracle` block lives in the all-arms report (`supervisor/swe_bench_mergeability.py:3299`).

Resolution: the public boundary is the composed artifact: powered factorial report plus separate all-arms diagnostic status.

### G3. The current repo cannot honestly satisfy P1.

Finding: current corpus artifacts report `candidate_count=0`, `n_good=0`, `n_bad=0`, and no produced predictions path. The AEB-0 all-arms artifact has `candidate_count=3`, `n_good=0`, `n_bad=0`, `status="unavailable"`, and `all_arms_populated=false`.

Resolution: commit an explicit blocked execution-status artifact and do not claim benchmark completion.

### G4. Report-only cannot be implied from "powered".

Finding: the generic powered evaluator can set `metric_applyable` true, while the SWE-bench Pro wrapper resets it false and exposes `powered_metric_applyable` for diagnostics (`supervisor/swe_bench_mergeability.py:4612`).

Resolution: P2 requires all authority flags false and allows only diagnostic powered readiness.

## Open Questions

None for implementation. The next real execution question is operational: when the linux/amd64 VM has Docker, disk, and an approved live solver command, run the scale corpus and then feed both reports into this checker.
