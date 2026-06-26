# Grill Findings TDD

Task id: `swe-bench-pro-oracle-adapter-20260626`

## Gate: TDD Review

Status: resolved

### Finding T1: The first tests must not run real Docker.

Evidence: The user explicitly required real Docker as execution artifact evidence, not RED unit tests. The TDD skill also requires mocking below system boundaries rather than replacing the decision under test.

Resolution: Unit tests fake subprocess and parser files below `run_swe_bench_pro_oracle(context)`. The real three-instance run is tracked as artifact work.

### Finding T2: A helper-only parser test would miss the bridge contract.

Evidence: `_normalise_oracle_adapter_outcome(...)` calls `_interpret_oracle_outcome(...)`, which rejects unknown statuses at `supervisor/swe_bench_mergeability.py` lines 393-400.

Resolution: Cycle 3 requires the adapter mapping to flow through the bridge normalization contract.

### Finding T2b: Pass-only parser coverage would miss fail semantics.

Evidence: `_interpret_oracle_outcome(...)` accepts both `pass` and `fail` for each status, and treats `pass_to_pass_status="fail"` as a regression at `supervisor/swe_bench_mergeability.py` lines 393-408.

Resolution: TDD Cycle 3 explicitly covers `fail/pass` and `pass/fail` well-formed parser outputs before implementation.

### Finding T3: Lowercase Pro fields must be covered by isolation tests.

Evidence: The current hidden scan only names uppercase `FAIL_TO_PASS`, `PASS_TO_PASS`, and `test_patch` in `SWEBENCH_PRO_HIDDEN_ORACLE_KEYS` at `supervisor/swe_bench_mergeability.py` lines 54-58.

Resolution: Cycle 4 expands the hidden-field scan and validates frozen/reviewer artifacts.

### Finding T4: Missing vendored scripts must be unavailable, not a test setup failure.

Evidence: Pro scripts are per-instance in the pinned repo, so a general adapter may encounter an unvendored row.

Resolution: Cycle 2 covers fail-closed behavior and Issue 6 limits vendoring to the first artifact's three rows.

## Accepted Ledger Record

```jsonl
{"stage":"tdd_grill_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/grill-findings-tdd.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
