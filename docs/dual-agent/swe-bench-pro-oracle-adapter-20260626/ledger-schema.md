# Ledger Schema

Task id: `swe-bench-pro-oracle-adapter-20260626`

Each ledger row is JSONL. All authority flags must remain false in this slice.

```json
{
  "stage": "prd_review|issues_review|tdd_review|tri_agent_review|red|green|artifact|commit|no_mistakes",
  "task_id": "swe-bench-pro-oracle-adapter-20260626",
  "status": "accepted|accepted_with_remediation|blocked|failed|passed",
  "evidence": ["path-or-test-nodeid"],
  "notes": "short operator-facing note",
  "authority_flags": {
    "metric_applyable": false,
    "improvement_claim_allowed": false,
    "powered_improvement_claim_allowed": false,
    "human_mergeability_claim_allowed": false,
    "default_change_allowed": false,
    "policy_mutated": false,
    "gate_advanced": false
  }
}
```

## Required Accepted Planning Rows

```jsonl
{"stage":"prd_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/prd.md","docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/grill-findings.md"],"notes":"PRD promises accepted after grill remediation.","authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
{"stage":"issues_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/issues.md"],"notes":"Eleven vertical tracer-bullet blocks accepted.","authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
{"stage":"tdd_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/tdd.md","docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/grill-findings-tdd.md"],"notes":"Boundary-first one-RED-one-GREEN plan accepted.","authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
