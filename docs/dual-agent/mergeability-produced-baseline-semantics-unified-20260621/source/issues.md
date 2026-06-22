# Issues

# Issues

## Slice 1: Normalize Baseline Receipts In SWE-bench Bridge And Fixture Runner

Priority: high

### PRD Promise

P1, P2, and P3.

### Public Boundary For First RED Test

Use `swebench_mergeability_fixture_runner` and `swebench_pro_mergeability_bridge_report`, then inspect emitted bridge rows and arm summaries.

### Scope:

Replace the fixture runner and direct bridge baseline fallback with produced-baseline receipt normalization. Missing, legacy boolean, self-report-only, malformed, and hash-mismatched rows become unavailable. Valid receipts populate provenance fields and baseline summary evidence.

### Acceptance Criteria:

- [ ] Missing produced receipts are unavailable, not accepted.
- [ ] Valid produced receipts populate candidate id, artifact hash, decision source, producer, prompt hash, and evidence kind.
- [ ] Legacy boolean baseline rows are unavailable.
- [ ] Report-only flags remain false.

### Blocked By

None - can start immediately.

## Slice 2: Preserve Produced Baseline Semantics Through Replay And Live Paths

Priority: high

### PRD Promise

P1, P2, and P3.

### Public Boundary For First RED Test

Use `swebench_mergeability_replay_runner` and `swebench_mergeability_live_runner`, then inspect generated replay reports and bridge summaries.

### Scope:

Thread normalized baseline provenance through frozen decisions, per-candidate artifacts, replay aggregation, and live generated manifests. Live generation may attach a produced baseline receipt only when generator output supplies an explicit accept or reject decision.

### Acceptance Criteria:

- [ ] Replay aggregation preserves valid produced baseline receipts.
- [ ] Live generation without explicit baseline decisions does not synthesize acceptance.
- [ ] Self-report remains calibration context only.
- [ ] `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, and `gate_advanced` remain false.

### Blocked By

Slice 1 must land first so replay and live aggregation preserve the same normalized receipt shape.
