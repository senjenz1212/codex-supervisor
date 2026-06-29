# TDD Grill Findings

Task id: `swe-bench-pro-candidate-corpus-20260626`

## Gate: TDD Review

Status: resolved

### Finding T1: The first RED test must hit the official replay loader boundary.

Evidence: The user names `_load_official_predictions(...)` as the P1 public boundary.

Resolution: Cycle 1 starts at that loader and not at a private hash helper.

### Finding T2: Generator tests may fake Docker only below the boundary.

Evidence: The user forbids silent mocking above the public boundary and allows live infra faked below it.

Resolution: Cycle 3 fakes solver/oracle results only as injected dependencies under the candidate-corpus builder seam.

### Finding T3: A blocked real execution is not a GREEN corpus.

Evidence: The current dependency real artifact is unavailable and the host has little free disk; real Docker commands hang before daemon output.

Resolution: Cycle 5 requires an artifact, but that artifact may be blocked. It must not create `pro-predictions.jsonl` unless the real corpus requirements are met.

### Finding T4: Non-applying candidates need their own exclusion path.

Evidence: P2 forbids counting non-applying patches as oracle-bad.

Resolution: Cycle 4 adds explicit non-applying exclusion coverage.

## Accepted Ledger Record

```jsonl
{"stage":"tdd_review","task_id":"swe-bench-pro-candidate-corpus-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/tdd.md","docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/grill-findings-tdd.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
