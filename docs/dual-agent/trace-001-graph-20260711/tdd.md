# TDD Plan: TRACE-001

## Existing Public-Boundary Coverage

1. `test_trace_identity_is_namespaced_versioned_and_rejects_bare_legacy_ids`
2. `test_closed_graph_returns_objective_to_promotion_trace_and_passes_closure`
3. `test_closure_rejects_short_promotion_path_that_skips_canonical_chain`
4. `test_requirement_without_test_blocks_unless_exact_signed_waiver_is_current`
5. `test_test_requires_runtime_evidence_and_a_pinned_run`
6. `test_runtime_evidence_must_descend_from_the_same_pinned_run`
7. `test_runtime_evidence_requires_a_grade_with_a_pinned_verifier`
8. `test_decision_requires_analysis_and_promotion_requires_decision`
9. `test_uncovered_node_blocks_and_validation_requires_explicit_aware_now`
10. `test_planning_validator_blocks_on_trace_closure_and_accepts_closed_graph`

These tests call public graph/planning seams rather than private traversal
helpers.

## Remaining Tracer Bullets

1. Persist and reload a graph without changing canonical identities or closure
   results.
2. Build nodes from one real workflow/run/evidence/grade sequence.
3. Reject a decision that cites a superseded grade without its invalidation.
4. Prove ledger artifact hashes and graph artifact hashes are the same values.
5. Exercise the graph in TRACER-001 instead of constructing a synthetic closed
   graph solely in a unit test.

## Verification

```text
.venv/bin/python -m pytest -q tests/test_trace_graph.py
```

Forbidden outcomes include helper-only path assertions, implicit wall-clock
time, namespace-free legacy IDs, and waivers that apply by loose matching.
