# Auto-Evolve Benchmark Ledger Schema

Every stage writes a JSONL record with this shape:

```json
{
  "schema_version": "supervisor-auto-evolve-benchmark-ledger/v1",
  "stage": "...",
  "issue_id": "...",
  "promise_ids": ["P..."],
  "aeb0_artifact_required": true,
  "aeb0_artifact_path": "",
  "aeb0_artifact_sha256": "",
  "artifact_path": "...",
  "artifact_sha256": "...",
  "evidence_kind": "...",
  "metric_source": "evaluator_execution|diagnostic_report|official_oracle|manual_review|read_only_sink",
  "evaluator_run_ref": "...",
  "evaluator_run_hash": "...",
  "metric_applyable": false,
  "improvement_claim_allowed": false,
  "powered_improvement_claim_allowed": false,
  "human_mergeability_claim_allowed": false,
  "default_change_allowed": false,
  "policy_mutated": false,
  "gate_advanced": false,
  "sink_in_trust_path": false,
  "gaming_flags": [],
  "blocked_reasons": [],
  "operator_review_required": true,
  "status": "blocked|report_only|accepted_for_draft_proposal|rejected|deferred"
}
```

## Stage Records

### real_official_all_arms_artifact_gate

- Issue: AEB-0.
- Promise IDs: P0, P1, P2, P6, P7.
- Evidence kind: `official_oracle`.
- Metric source: `official_oracle`.
- Status values: `report_only`, `blocked`.
- Required blocked reasons: missing real artifact, missing CLI prerequisite, official replay unavailable, baseline unavailable, S_probe unavailable, S_full unavailable, oracle ceiling unavailable, reviewer public evidence unavailable, hidden leak, insufficient oracle good/bad, matched TAR unavailable, Verified smoke only.

### official_all_arms_evidence_packet_hardening

- Issue: AEB-1.
- Promise IDs: P1, P2, P6, P7, P10.
- Evidence kind: `official_oracle`.
- Metric source: `official_oracle`.
- Status values: `report_only`, `blocked`.
- Required blocked reasons: missing AEB-0, malformed baseline, missing or malformed oracle bucket, missing reviewer public evidence, hidden leak.

### powered_factorial_benchmark

- Issue: AEB-2.
- Promise IDs: P1, P2, P5, P6, P10.
- Evidence kind: `diagnostic_report`.
- Metric source: `diagnostic_report`.
- Status values: `report_only`, `blocked`.
- Required blocked reasons: missing AEB-0, underpowered, missing MDE/alpha/power/CI width, insufficient paired discordance, baseline unavailable, reviewer panel unavailable, gaming flags.

### reviewer_independence_measurement

- Issue: AEB-3.
- Promise IDs: P3, P4, P7, P10.
- Evidence kind: `diagnostic_report`.
- Metric source: `diagnostic_report`.
- Status values: `report_only`, `blocked`.
- Required blocked reasons: missing AEB-0 for official-row linkage, zero oracle-grounded reviewer errors, requires at least two reviewers, fixture-only evidence, same-family decisive vote, cursor/default provider family unproven.

### benchmark_to_autoresearch_evidence_conversion

- Issue: AEB-4.
- Promise IDs: P5, P8, P10.
- Evidence kind: `evaluator_execution`.
- Metric source: `evaluator_execution`.
- Status values: `blocked`, `rejected`, `report_only`.
- Required blocked reasons: missing AEB-0, missing evaluator run ref/hash, missing empty-floor comparison, missing positive delta, missing evaluator-quality controls, missing operator review, missing policy overlay candidate ref, underpowered benchmark, gaming flags.

### policy_derivation

- Issue: AEB-5.
- Promise IDs: P8, P9, P10.
- Evidence kind: `manual_review`.
- Metric source: `evaluator_execution`.
- Status values: `accepted_for_draft_proposal`, `blocked`, `rejected`.
- Required blocked reasons: missing AEB-0, report applyability error, record applyability error, missing empty-floor win, missing positive metric delta, missing overlay candidate ref, gaming flags.

### translation_audit

- Issue: AEB-6.
- Promise IDs: P0, P1, P2, P3, P4, P5, P6, P7, P8, P9, P10.
- Evidence kind: `diagnostic_report`.
- Metric source: `manual_review`.
- Status values: `blocked`, `report_only`.
- Required blocked reasons: promise without issue, issue without public-boundary RED test, helper-only first test, missing AEB-0 dependency, AutoResearch bypass, mutation before approval, sink in trust path.

### observability_sink_deferred

- Issue: future separate packet.
- Promise IDs: P10.
- Evidence kind: `read_only_sink`.
- Metric source: `read_only_sink`.
- Status values: `deferred`, `blocked`.
- Required blocked reasons: missing AEB-0 artifact, sink in trust path, sink attempted authority flag override, annotation queue not yet proven.

## Invariants

- `operator_review_required` is always true for benchmark-to-policy records.
- `default_change_allowed`, `policy_mutated`, and `gate_advanced` are false until an explicit policy approval path runs.
- `improvement_claim_allowed` is false on benchmark reports. It may become policy-derivable only inside accepted AutoResearch records that satisfy policy-evolution applyability, not in benchmark reports.
- Benchmark `metric_applyable` is not policy applyability. It must not be consumed by policy derivation directly.
- `powered_improvement_claim_allowed` and `human_mergeability_claim_allowed` are false on benchmark reports.
- `artifact_sha256` is required for every non-empty `artifact_path`.
- Empty `evaluator_run_ref` or `evaluator_run_hash` blocks any `metric_source=evaluator_execution` evidence conversion.
- `sink_in_trust_path` is always false. Sink-origin traces, scores, annotations, dashboards, or IDs cannot satisfy evaluator provenance, quality controls, effective-vote, roster selection, or policy derivation.
