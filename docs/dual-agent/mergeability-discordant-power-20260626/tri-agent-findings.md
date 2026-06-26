# Tri-Agent Findings

Exactly three read-only validation subagents were spawned for `mergeability-discordant-power-20260626`. They were not asked to edit files and were not closed.

## Agent A: Statistical Correctness

Agent id: `019f02ab-9bfc-7e61-96d3-5d4c50bb6818`

Verdict: block.

Strongest objection: current implementation permitted `metric_applyable=True` with zero discordant pairs and no `paired_power` block.

Required file:line evidence:

- `_paired_discordant_counts` only returned cells before this change: `supervisor/mergeability_bench.py:4952`.
- Raw sample-size sufficiency was count-only: `supervisor/mergeability_bench.py:5370`.
- `metric_applyable` was wired from raw powered status: `supervisor/mergeability_bench.py:2017`.
- The old regression expectation allowed raw-threshold applyability: `tests/test_mergeability_bench.py:2501`.

Changes folded back:

- TDD Cycle 1 keeps all-concordant rows as the first public-boundary RED.
- TDD Cycle 3 adds a named continuity-corrected McNemar chi-square threshold RED.
- Existing raw-threshold regression expectations were updated during implementation.

## Agent B: Gate Wiring

Agent id: `019f02ab-b2ea-7d53-808e-650754133d91`

Verdict: revise.

Strongest objection: the original TDD plan did not explicitly prove that sparse nonzero discordance keeps `paired_power.status="underpowered"` and `metric_applyable=False`.

Required file:line evidence:

- Current `metric_applyable` gate used raw sample-size status: `supervisor/mergeability_bench.py:2017`.
- Current promotion guardrail wrote raw powered threshold status: `supervisor/mergeability_bench.py:2043`.
- The PRD requires raw counts to remain descriptive, not authoritative.

Changes folded back:

- TDD Cycle 2 asserts nonzero sparse discordance, raw sample-size sufficiency, underpowered paired status, and `metric_applyable=False`.
- Issue 5 requires distinct raw sample-size and paired-power guardrail signals.
- Authority tests pin nested guardrails, non-applyable recommendation, and empty policy derivation.

## Agent C: CI Honesty

Agent id: `019f02ab-d1f0-7793-b798-250df0685d17`

Verdict: revise.

Strongest objection: the original plan could pass with FAR/TAR coverage only while leaving FRR projection or zero-event TAR/FRR unproven.

Required file:line evidence:

- PRD requires every FAR/TAR/FRR rate to carry intervals.
- SWE `far_tar_frr` projection included FAR/TAR intervals but omitted FRR interval projection: `supervisor/swe_bench_mergeability.py:695`.
- Powered factorial added FRR CI separately: `supervisor/mergeability_bench.py:1968`.
- `_wilson_interval` lacked a zero-event rule-of-three branch: `supervisor/mergeability_bench.py:5444`.

Changes folded back:

- TDD Cycle 4 checks zero-event FAR, TAR, and FRR rule-of-three behavior.
- TDD Cycle 5 requires FRR interval projection in `far_tar_frr` and non-Wald interval provenance.

## Resolution

The packet was revised before implementation. `ledger.jsonl` records the initial tri-agent revise state and the accepted second revisions of PRD, issues, and TDD.
