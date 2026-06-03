# PRD Grill Findings

Task id: `reviewer-panel-calibrated-weighting-20260602`

### Finding 1: calibration must be data lineage, not config constants

Status: resolved

Evidence: prior eval runner output was observation-only and explicitly avoided
active weights. A config-only `reviewer_weight=0.5` would satisfy no measured
dependency promise.

Resolution: PRD-001 requires a versioned calibration artifact with report hash,
labeled set hash, pairwise dependency signals, and calibration hash. The
workflow consumes that artifact only when it exists.

### Finding 2: correlated accept escalation can create throughput regressions

Status: resolved

Evidence: the conservative aggregator currently advances clean high-confidence
accepts. Applying calibration without a fallback or threshold guard could block
normal work.

Resolution: PRD-002 separates effectively independent accept from measured
correlated accept. PRD-004 requires conservative fallback when no artifact
exists.

### Finding 3: weighting must never weaken a real block

Status: resolved

Evidence: previous slices intentionally made important/critical revise or deny
hard-block. Weighted accept logic must not average those away.

Resolution: PRD-003 keeps real important/critical revise and deny handling
ahead of calibrated accept aggregation. Calibration only changes the all-accept
path.

### Finding 4: adjudication remains separate from weighting

Status: resolved

Evidence: adjudication protects disagreement and strong objections by inspecting
cited evidence. Weighting is an accept-confidence decision, not majority vote.

Resolution: scope preserves `adjudicate_reviewer_panel`; calibrated weighting
runs before/alongside it but does not replace the adjudication packet.

### Finding 5: "severity-weighted" must not be only prose

Status: resolved

Evidence: a dependency-only formula can satisfy correlated reviewer escalation
while ignoring the severity term in the requested dependence/severity-weighted
aggregation.

Resolution: PRD-002 now requires severity multipliers in the active accept
calculation, while PRD-003 keeps important and critical real revise/deny
verdicts as hard blocks before weighting.

### Finding 6: active calibration needs lineage validation

Status: resolved

Evidence: accepting a payload with only `schema_version` plus
`reviewer_weights` would let manually assigned constants activate the calibrated
path, contradicting the measured-dependency requirement.

Resolution: PRD-001 and the TDD plan require loader validation for source
report hash, labeled-set hash, pairwise dependency, per-reviewer
derived-from-pairwise lineage, and a matching calibration digest before active
weights are used.
