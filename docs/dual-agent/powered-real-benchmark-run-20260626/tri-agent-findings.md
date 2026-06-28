# Tri-Agent Findings

Task id: `powered-real-benchmark-run-20260626`

Exactly three read-only validation subagents were used. Each prompt ended with `Do not edit files. Do not close any agents.`

## A. Powered For Real

Verdict: accept with required tightening.

Evidence:

- The powered evaluator requires both raw sample-size sufficiency and paired-power sufficiency (`supervisor/mergeability_bench.py:1812`).
- The required report fields are `sample_size_sufficiency`, `paired_discordant_counts.full_supervisor_stack`, and `paired_power.comparisons.full_supervisor_stack`.
- Current artifacts have no powered factorial report and no artifact satisfying `>=30` oracle-good, `>=30` oracle-bad, `>=25` discordant pairs, and McNemar `p<=0.05`.

Folded back:

- P1 and the checker require the paired McNemar gate, not just raw counts.
- The blocked artifact states that no powered report exists.

## B. Honest Labeling And Report-Only

Verdict: accept with required tightening.

Evidence:

- The all-arms report emits `benchmark_oracle.kind="swe_bench_held_out_test_pass_proxy"` and `maintainer_mergeability_claim_allowed=false` (`supervisor/swe_bench_mergeability.py:3299`).
- The powered Pro wrapper resets `metric_applyable=false` and preserves false authority flags while exposing `powered_metric_applyable` diagnostically (`supervisor/swe_bench_mergeability.py:4612`).
- Powered reports do not currently include the all-arms `benchmark_oracle` block, so the DoD must validate the composed artifact.

Folded back:

- The checker accepts powered report plus all-arms diagnostic status.
- P2 explicitly allows diagnostic `powered_metric_applyable` but rejects any authority flag true.

## C. Scale Provenance

Verdict: not enough current evidence.

Evidence:

- `docs/dual-agent/pro-corpus-generate-label-20260626/artifacts/corpus-generate-label-execution-status.json` reports `status="blocked"`, `candidate_count=0`, `n_good=0`, `n_bad=0`, and no produced predictions path.
- `docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/artifacts/candidate-corpus-execution-status.json` reports `status="unavailable"`, `candidate_count=0`, `n_good=0`, `n_bad=0`.
- The current AEB-0 input is a three-row gold-patch smoke input, not oracle-bad scale evidence.

Folded back:

- The checker rejects fixture, synthetic, smoke, and gold-only source paths.
- The execution artifact is blocked and gives the next unblocked VM step.
