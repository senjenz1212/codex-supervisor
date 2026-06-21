# PRD Grill Findings

### Finding 1

status: resolved

The original slice could have hidden the real problem by allowing S_full to remain unavailable whenever the caller forgot to pass a panel function. The PRD now names configured reviewer-panel mode as the product promise and requires the measurement boundary to use the reviewer registry roster instead of the unavailable fallback.

### Finding 2

status: resolved

The reviewer-panel adapter could accidentally become a second aggregation implementation. The design now keeps `evaluate_reviewer_panel` as the aggregation authority and limits new code to translating mergeability reviewer packets into the existing reviewer request and result shape.

### Finding 3

status: resolved

Oracle isolation must be structural, not reviewer etiquette. The PRD now requires `_public_input_oracle_refs` to guard the packet before reviewer invocation and to make S_full unavailable or rejected when forbidden oracle material appears.

### Finding 4

status: resolved

Calibration evidence could be overclaimed as an improvement. The PRD now keeps `metric_applyable`, `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` false for this slice.
