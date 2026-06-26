# PRD Grill Findings

## Gate

Skill sequence: `prd-to-tdd` -> `to-prd` -> `codebase-design` for seams/interfaces -> `grill-with-docs`.

Decision: pass after revising the packet so AEB-0 is the root gate. No implementation may start yet.

## Findings

### G1. AEB-0 must be the root gate, not an implied blocker

- Risk: A later bridge, sink, annotation, or policy issue can appear ready before real all-arms evidence exists.
- Evidence: No committed real `official_all_arms_diagnostic_report.json` was found in this packet; the all-arms runner writes that artifact at `supervisor/swe_bench_mergeability.py:2643` and blocks completion on missing arm/reviewer/oracle prerequisites around `supervisor/swe_bench_mergeability.py:2782`.
- Resolution: Added P0 and AEB-0. All downstream real-evidence claims now depend on AEB-0.

### G2. Benchmark report flags must not become policy authority

- Risk: The phrase "real benchmark" can tempt implementers to set `metric_applyable=true` or `improvement_claim_allowed=true` on official/all-arms reports.
- Evidence: Official all-arms report explicitly keeps `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` false at `supervisor/swe_bench_mergeability.py:2944`.
- Resolution: P2 and AEB-0/AEB-1 forbid flipping diagnostic flags directly. Evidence conversion must go through AutoResearch records.

### G3. "Powered" must be stronger than class counts

- Risk: Existing `_factorial_sample_size_sufficiency` uses `min_good`/`min_bad` thresholds, which is useful but insufficient for the requested power contract.
- Evidence: The current function records only `n_bad`, `n_good`, `min_bad`, and `min_good` at `supervisor/mergeability_bench.py:5370`.
- Resolution: P5 and AEB-2 require target MDE, alpha, power, CI width, paired discordance, and AEB-0 dependency status before evidence conversion.

### G4. Effective-vote claims need oracle-grounded reviewer errors

- Risk: Reviewer disagreement can look like independence even when no reviewer has oracle-grounded errors.
- Evidence: `_effective_vote_estimate` returns `zero_oracle_grounded_reviewer_errors` when total reviewer errors are zero at `supervisor/mergeability_bench.py:5254`; roster selection guard requires oracle-grounded errors at `supervisor/mergeability_bench.py:5314`.
- Resolution: P4 and AEB-3 make disagreement-only evidence forbidden and keep roster metrics measurement-only.

### G5. AutoResearch acceptance is not enough without evaluator-quality controls

- Risk: A bridge could emit accepted-looking records that omit determinism, no-op, harmful, known-good, empty-floor, or provenance evidence.
- Evidence: `_record_applyability_error` requires evaluator execution provenance/hash at `supervisor/autoresearch/policy_evolution.py:522`, and `_record_quality_control_error` requires supervisor-generated runtime-native controls at `supervisor/autoresearch/policy_evolution.py:547`.
- Resolution: P8/P10 and AEB-4 require converted records to satisfy AutoResearch validation and policy-evolution applyability.

### G6. Draft proposals are allowed; mutation is not

- Risk: "Auto evolve" can sound like automatic mutation.
- Evidence: `derive_policy_evolution_proposals_from_report` creates proposals at `supervisor/autoresearch/policy_evolution.py:76`; authority invariants keep `default_change_allowed=false`, `automatic_policy_mutation=false`, and `gate_advanced=false` at `supervisor/autoresearch/policy_evolution.py:827`.
- Resolution: P9 and AEB-5 state that auto-evolution stops at draft proposal until operator approval.

### G7. Official oracle bucket integrity needs exact wording

- Risk: A malformed official harness report without `FAIL_TO_PASS` or `PASS_TO_PASS` buckets could be scored as pass, while an empty-but-present bucket should remain compatible with upstream empty-bucket semantics.
- Evidence: `_status_for` currently returns `pass` when the bucket is absent/non-mapping at `supervisor/swe_bench_official_oracle.py:283`.
- Resolution: AEB-1 distinguishes empty-present buckets from missing/malformed buckets.

### G8. Observability sinks must stay outside the trust path

- Risk: Langfuse/Opik traces, scores, annotations, or dashboard URLs could be mistaken for evaluator evidence.
- Evidence: Policy derivation consumes accepted AutoResearch `records[]` and blocks on report/record applyability at `supervisor/autoresearch/policy_evolution.py:500`; reviewer effective-vote logic is computed from oracle-grounded reviewer errors at `supervisor/mergeability_bench.py:5254`.
- Resolution: P10, translation audit, ledger invariants, and `next-supervisor-prompts.md` defer sink work and require read-only sink-never-in-trust-path tests.

## Status

All PRD findings are resolved in the revised PRD, issue pack, ledger schema, translation audit, and next prompts.
