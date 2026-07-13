# TDD Plan and Execution Record

## Public Seams

- `ClaimGate.max_claim_level`
- `ClaimGate.derive_report`
- `ClaimGate.validate_report`
- Public report producer functions exercised by their existing focused tests
- Repository source scan for managed-field literals

## RED → GREEN Sequence

### 1. Fixture Replay Resolves to L1

Test: `test_fixture_replay_bundle_resolves_to_l1`

- RED: test collection failed because `supervisor.claim_gate` did not exist.
- GREEN: added `ClaimLevel` and cumulative L0/L1 predicates for pins, hashes,
  artifacts, and traceable detector evidence.

### 2. Hidden Verifier Is Mandatory for L2

Test: `test_l2_requires_an_independent_hidden_verifier`

- RED: valid hidden-verifier evidence still returned L1.
- GREEN: required independent, hidden, identified, pinned verifier results.
- Negative assertion: changing `independent` to false returns L1.

### 3. Randomized Powered B-vs-C Gates L3

Test: `test_l3_requires_a_positive_randomized_powered_b_vs_c_result`

- RED: qualifying causal evidence returned L2.
- GREEN: required B-vs-C, randomized, powered, positive improvement, and a
  pinned analysis.
- Negative assertion: A-vs-B returns L2.

### 4. Replication, ROI, and Auto-Improvement Gate L4–L6

Tests:

- `test_l4_requires_replication_across_distinct_strata`
- `test_l5_requires_measured_operating_cost_and_positive_roi`
- `test_l6_requires_frozen_control_sealed_holdout_and_passing_canary`

Each test first failed at the prior level, then gained the minimum predicate
for the next level and retained a negative assertion for missing evidence.

### 5. Producer Flags Are Gate-Owned

Tests:

- `test_report_producer_cannot_manually_set_improvement_claim_flag`
- `test_nested_manual_claim_flag_is_also_rejected`
- `test_report_improvement_flags_are_derived_from_claim_level`

- RED: the typed manual-field error did not exist; nested input was initially
  accepted.
- GREEN: recursive manual-field detection rejects both managed names and the
  gate injects false below L3 / true at L3.

### 6. Forbidden Claims Fail Closed

Tests:

- `test_report_asserting_forbidden_improvement_claim_is_rejected`
- `test_fixture_replay_report_cannot_assert_l3`
- `test_causal_evidence_allows_registered_improvement_claim`

- RED: the unsupported-claim type and validator did not exist.
- GREEN: registered IDs and phrases resolve to minimum levels; unknown or
  under-evidenced claims raise `UnsupportedClaimError`.

### 7. Producer Migration Regression

Test: `test_producers_do_not_literal_assign_claim_gate_owned_flags`

The test parses production Python ASTs under `supervisor/` and `scripts/`.
Literal assignments to either managed field fail. Existing tests may still
construct manual fields to prove rejection behavior.

## Coverage Index

| Promise | Tests |
|---|---|
| P1 | L1, L3, L4, L5, L6 level tests |
| P2 | hidden-verifier requirement test |
| P3 | manual, nested manual, derived output, producer AST tests |
| P4 | forbidden phrase, asserted L3, supported L3 tests |
| P5 | YAML/runtime consistency and documentation presence checks |

## Focused Verification

```text
uv run pytest -q tests/test_claim_gate.py tests/test_program_001_claim_gate.py
uv run pytest -q tests/test_auto_evolve_benchmark_observability_sink.py
uv run pytest -q tests/test_autoresearch_benchmark_promotion.py
uv run pytest -q tests/test_powered_real_benchmark_dod.py
uv run pytest -q tests/test_pro_oracle_gold_proof.py tests/test_swebench_pro_label_stability.py
uv run pytest -q <focused mergeability producer nodes>
python3 -m py_compile supervisor/claim_gate.py <modified producer modules>
git diff --check
```
