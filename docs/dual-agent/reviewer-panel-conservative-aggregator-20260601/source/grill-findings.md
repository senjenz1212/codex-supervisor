# PRD Grill Findings: Reviewer Panel Conservative Aggregator

### Finding 1: Do not let panel aggregation bypass reviewer-unavailable policy

Status: resolved

Question: Could a missing reviewer verdict be handled both by the new panel
evaluator and by the existing reviewer-unavailable recovery branch, creating
conflicting decisions?

Resolution: The PRD now separates quality aggregation from infrastructure
recovery. Recoverable reviewer infrastructure failures continue through the
existing reviewer-unavailable policy branch, which records degraded evidence or
human escalation. The panel evaluator still records missing reviewer inputs, but
the recovery branch owns degraded advance decisions.

### Finding 2: Keep the low-confidence threshold permissive until eval data

Status: resolved

Question: If low-confidence accepts escalate, could this slice create false
escalations on the single working reviewer before confidence calibration exists?

Resolution: The PRD requires a tunable low-confidence threshold with a
permissive default. Tests must prove a normal high-confidence accept still
advances by default, while an explicitly configured stricter threshold can
escalate a low-confidence accept.

### Finding 3: Preserve the public boundary for the first test

Status: resolved

Question: Would helper-only evaluator tests miss workflow regressions in Codex
decision composition, reviewer-unavailable recovery, or event payloads?

Resolution: The PRD and TDD plan require first RED tests at the
`run_dual_agent_workflow` public boundary. Helper tests are allowed only after
the public path proves the behavior.

### Finding 4: Do not add weighting under the name conservative

Status: resolved

Question: Could provider lineage, confidence, and severity metadata tempt this
slice into early calibrated weighting without eval data?

Resolution: The PRD explicitly excludes weighting and lineage correlation. The
only confidence behavior is a threshold-based escalation for accepting
reviewers, and serious revise/deny decisions remain hard blocks.
