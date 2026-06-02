# TDD Grill Findings: Reviewer Panel Conservative Aggregator

### Finding 1: First RED tests must not mock away workflow decision composition

Status: resolved

Question: Do the planned tests exercise the public workflow boundary, or only a
new helper?

Resolution: The first tests all call `run_dual_agent_workflow` with fake
external reviewer runners below the boundary. The workflow route, panel result
mapping, reviewer-unavailable recovery, Codex decision composition, and event
writing remain in play.

### Finding 2: Missing verdict needs a separate test from infrastructure recovery

Status: resolved

Question: Could missing verdict handling be conflated with the existing
reviewer-unavailable policy tests?

Resolution: The TDD plan includes one malformed or missing-verdict test with no
recoverable infrastructure classification, and one reviewer-unavailable
recovery regression. This keeps quality/missing semantics separate from
classified infrastructure recovery.

### Finding 3: Throughput regression must be explicitly tested

Status: resolved

Question: Does the plan prove a normal single-reviewer accept still advances?

Resolution: The TDD plan includes a high-confidence accept test under default
configuration. This protects the existing working Gemini/LiteLLM rigorous path
from accidental over-escalation.

### Finding 4: Event evidence must include the aggregate decision, not only raw inputs

Status: resolved

Question: If only per-reviewer inputs are exported, can replay explain why the
gate advanced or blocked?

Resolution: The TDD plan includes an event/export test that expects
`independent_reviewer_panel_decision` on both new and legacy reviewer event
payloads.
