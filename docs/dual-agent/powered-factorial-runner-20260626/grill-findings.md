# PRD Grill Findings

Task id: `powered-factorial-runner-20260626`

## Finding 1: Do not blur all-arms readiness with powered readiness.

Resolution: The PRD names a distinct powered factorial runner and keeps the official all-arms diagnostic unchanged. All-arms can remain diagnostic and blocked while the powered runner consumes the Pro corpus through a separate CLI path.

## Finding 2: The Pro corpus must be the source of truth.

Resolution: The runner records the predictions JSONL path and candidate hashes in the powered report. The generated mergeability manifest is only an adapter artifact for the existing evaluator interface, not a substitute corpus.

## Finding 3: A qualified power contract must not become an automatic policy bridge.

Resolution: The report emits `evidence_conversion_power_contract` while preserving false mutation flags. Later AutoResearch conversion remains out of scope.

## Finding 4: Missing arm decisions must fail closed.

Resolution: The PRD forbids defaulting missing Pro arm decisions into a qualified result. Tests must prove missing required evidence cannot produce `status="qualified"`.

## Ledger Record

```jsonl
{"stage":"prd_review","task_id":"powered-factorial-runner-20260626","status":"accepted","evidence":["docs/dual-agent/powered-factorial-runner-20260626/prd.md","docs/dual-agent/powered-factorial-runner-20260626/grill-findings.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
