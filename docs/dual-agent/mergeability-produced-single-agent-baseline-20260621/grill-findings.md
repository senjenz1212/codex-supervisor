## Findings

1. Resolved: The legacy metadata arm is not a real baseline and must not be renamed in a way that hides its source. The PRD now requires both compatibility preservation and explicit `metadata_accept_all_baseline` labeling.
2. Resolved: A produced baseline without receipt validation would recreate the same evidentiary weakness under a new name. The PRD now requires candidate identity, prompt hash, producer labels, artifact hash, decision, and unavailable handling.
3. Resolved: Matched-TAR comparisons against unavailable baseline rows would be misleading. The PRD now requires unavailable status instead of computing from imputed reject rows.
4. Resolved: Calibration evidence must not become policy evidence. The PRD keeps `metric_applyable`, `improvement_claim_allowed`, policy mutation, and proposal derivation off.

## Stage Verdict

pass
