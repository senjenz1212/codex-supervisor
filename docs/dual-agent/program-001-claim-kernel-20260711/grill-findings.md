# PRD Grill Findings

### Finding 1: L3 Must Mean Positive Improvement, Not Merely Experiment Execution

status: resolved

A randomized powered B-vs-C experiment can find no effect or harm. The L3
predicate therefore requires `supports_improvement: true` plus a pinned
analysis, not only randomized and powered booleans.

### Finding 2: L5 Needs a Positive ROI Result

status: resolved

Recording operating cost alone does not establish ROI. L5 requires measured
non-negative cost, a pinned analysis, and `supports_positive_roi: true`.

### Finding 3: Bare Evidence Booleans Would Recreate Manual Authority

status: resolved

Each level requires identity and artifact/result references. Verifier,
analysis, replication, control, holdout, and canary records cannot be replaced
by a top-level `true`.

### Finding 4: Producer Overrides Can Hide Below the Top Level

status: resolved

Manual-field detection recursively scans mappings and sequences. A nested
`authority_flags.powered_improvement_claim_allowed` is rejected like a
top-level field.

### Finding 5: Historical Benchmarks Must Not Be Upgraded by Mapping

status: resolved

`legacy-map.yaml` labels entries as reference-only and records explicit
authority caveats. Mapping an artifact into the program graph does not raise
its ClaimGate level.
