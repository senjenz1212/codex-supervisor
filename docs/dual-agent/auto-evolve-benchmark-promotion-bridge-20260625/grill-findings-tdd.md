# TDD Grill Findings

## Gate

Skill sequence: `prd-to-tdd` -> `tdd` -> `grill-with-docs`.

Decision: pass for revised planning; implementation remains blocked until the issue pack is accepted and the first RED test for AEB-0 is written.

## Findings

### T1. AEB-0 must be the first RED sequence

- Risk: Implementation could start with bridge helper tests before proving real all-arms artifact readiness.
- Resolution: TDD now starts with missing real artifact, missing CLI prerequisites, missing baseline/oracle receipts, Verified smoke-only framing, and false authority flags.

### T2. New evidence conversion bridge must have a public seam before helper tests

- Risk: Implementation could start by testing private conversion helpers.
- Resolution: AEB-4 names `promote_benchmark_report_to_autoresearch_report` as the first public seam and starts with missing AEB-0/evaluator hash boundary tests.

### T3. AEB-2 first RED must fail on the current raw-count power gap

- Risk: Existing sample sufficiency only records raw `n_good`/`n_bad`.
- Evidence: `_factorial_sample_size_sufficiency` records `min_bad` and `min_good` at `supervisor/mergeability_bench.py:5370`.
- Resolution: AEB-2 Cycle 1 requires MDE/alpha/power/CI-width evidence and AEB-0 dependency status.

### T4. AEB-5 must not test approval/apply in the derivation slice

- Risk: Draft proposal derivation and policy application can get mixed.
- Evidence: `derive_policy_evolution_proposals_from_report` creates proposals at `supervisor/autoresearch/policy_evolution.py:76`, while `approve_policy_proposal` is the approval/apply path at `supervisor/autoresearch/policy_evolution.py:189`.
- Resolution: AEB-5 tests derivation only and explicitly forbids calling approval/apply.

### T5. Ledger tests must preserve false authority flags in blocked states

- Risk: Blocked/report-only evidence can be dropped or summarized too loosely.
- Resolution: AEB-6 starts with a blocked ledger record test that asserts `metric_applyable=false`, `improvement_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, `gate_advanced=false`, and `real_official_all_arms_artifact_required`.

### T6. Live dependencies must be fake below the boundary

- Risk: First tests could call live Docker/SWE-bench/reviewer/model/observability infrastructure.
- Resolution: TDD contract states live SWE-bench fetch, Docker, reviewer panel, observability sink, and model calls are mocked below public boundaries while preserving real-input-shaped fixtures.

### T7. Official oracle parser must fail closed before live evidence claims

- Risk: The official adapter can run real `swebench.harness.run_evaluation`, but malformed/missing status buckets should not become pass.
- Resolution: AEB-1 TDD splits empty-present pass-compatible buckets from missing/malformed unavailable buckets.

### T8. Sink tests are deferred but specified

- Risk: Langfuse/Opik could be added later without trust-path guardrails.
- Resolution: AEB-6 and `next-supervisor-prompts.md` specify read-only sink tests and defer sink implementation until AEB-0 exists.

## Status

All TDD findings are resolved in `tdd.md`, `issues.md`, `translation-audit.md`, and `next-supervisor-prompts.md`. Do not advance to implementation if any issue is split into helper-only tests.
