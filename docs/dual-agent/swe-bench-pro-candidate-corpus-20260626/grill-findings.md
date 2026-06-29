# Grill Findings

Task id: `swe-bench-pro-candidate-corpus-20260626`

## Gate: PRD Review

Status: resolved

### Finding G1: Gold-only Pro predictions make FAR degenerate.

Evidence: `_summarize_acceptance_arm(...)` sets `n_bad` from the oracle-negative denominator, and `_rate(...)` returns `0.0` when the denominator is zero.

Resolution: P1 requires at least one oracle-bad candidate before any corpus can be called FAR-capable.

### Finding G2: Bad candidates cannot be dataset-derived.

Evidence: SWE-bench Verified and SWE-bench Pro ship gold patches, not model-generated non-resolving patches.

Resolution: P1 and P2 require oracle-bad candidates to originate from real single-agent solver attempts and be labeled by the Slice-1 Pro oracle.

### Finding G3: "Tests fail" is not enough if the patch never applied.

Evidence: The Pro oracle adapter applies the model patch before running selected tests. If patch application or parser output is unavailable, the result is infrastructure/unavailable evidence, not a negative candidate.

Resolution: P2 excludes non-applying, missing-output, malformed-output, timeout, Docker, and ENOSPC cases from `oracle-bad`.

### Finding G4: The current host may not be able to produce the requested real corpus.

Evidence: The dependency packet's real Pro oracle artifact is `status="unavailable"` with Docker/runtime/ENOSPC blockers, and current local disk is near full.

Resolution: The PRD permits a blocked execution artifact but forbids presenting it as `pro-predictions.jsonl` with `n_bad > 0`.

### Finding G5: Loader provenance is part of the public boundary.

Evidence: `_load_official_predictions(...)` currently loads candidate rows before official replay consumes them. Dropping provenance there would erase the audit trail.

Resolution: P1 makes provenance and hashes part of the loader contract, not a sidecar-only artifact.

## Accepted Ledger Record

```jsonl
{"stage":"prd_review","task_id":"swe-bench-pro-candidate-corpus-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/prd.md","docs/dual-agent/swe-bench-pro-candidate-corpus-20260626/grill-findings.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
