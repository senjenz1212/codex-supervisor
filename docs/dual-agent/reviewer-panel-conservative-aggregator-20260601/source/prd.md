# PRD: Reviewer Panel Conservative Aggregator

## Problem Statement

The reviewer panel foundation exposes `independent_reviewer_results[]`, but the
workflow still decides gates through the legacy single-reviewer
`cursor_accepts(...)` branch. That leaves the new panel schema as trace metadata
only: missing verdicts, low-confidence accepts, and future multi-reviewer
results do not have explicit deterministic semantics at the gate boundary.

Operators need a conservative panel decision that uses the panel results without
weakening the current gates. The system must still advance clean
high-confidence single-reviewer accepts, but it must block real important or
critical objections, never count a missing verdict as accept, and escalate
low-confidence accepts instead of quietly advancing them.

## Solution

Add a small deterministic panel evaluator for `independent_reviewer_results[]`.
The evaluator returns a panel decision, reason, threshold, blocking reviewers,
low-confidence reviewers, missing reviewers, and the reviewer inputs used. Wire
that decision into `run_dual_agent_workflow` while preserving existing
reviewer-unavailable recovery and legacy `cursor_review` compatibility.

The initial low-confidence threshold is intentionally permissive and tunable in
configuration so this slice establishes semantics without creating throughput
regressions before eval data exists. Aggregation is non-weighted: any real
critical or important revise/deny hard-blocks, available real accepts can
advance, low-confidence accepts escalate, and missing or malformed verdicts
never count as accept.

## User Stories

1. As a workflow operator, I want a clean high-confidence independent reviewer
   accept to advance as it does today, so that the panel foundation does not
   slow ordinary rigorous runs.
2. As a reviewer of gates, I want an important or critical revise/deny from any
   real reviewer to block, so that serious objections cannot be averaged away.
3. As an auditor, I want missing reviewer verdicts to be recorded as missing
   and never counted as accept, so that unavailable evidence is not silently
   transformed into approval.
4. As a workflow owner, I want low-confidence accepts to escalate rather than
   auto-advance when configured, so that uncertain approvals get human or
   recovery attention.
5. As a maintainer, I want the panel decision and per-reviewer inputs recorded
   on reviewer events and gate payloads, so that replay explains why a gate
   advanced, blocked, or escalated.
6. As a reliability owner, I want existing reviewer-unavailable recovery to
   remain intact, so that infrastructure failures keep using the degraded or
   human-resume policy instead of being confused with quality objections.

## PRD Promise Contracts

P1. important-reviewer-objection-blocks

User-visible promise: A real independent reviewer revise or deny at critical or
important severity hard-blocks the gate.
Representative prompts or actions: Run `run_dual_agent_workflow` with cursor
review enabled and a reviewer outcome whose decision is `revise` or `deny` and
critical review severity is `important` or `critical`.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the gate does not advance, Codex records a non-accept
decision, the panel decision names the blocking reviewer, and the legacy
single-reviewer regression still blocks.
Forbidden outcomes: the gate advances, a serious objection is treated as an
accept, degraded reviewer-unavailable recovery overrides a real objection, or
the reviewer decision is hidden from artifacts.
Related user stories: 2, 5, 6

P2. missing-reviewer-verdict-never-accepts

User-visible promise: A missing or malformed reviewer verdict never counts as an
accept in the panel decision.
Representative prompts or actions: Run `run_dual_agent_workflow` with a reviewer
result that has no typed outcome and no recoverable reviewer-unavailable
classification.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the panel decision records missing reviewers and prevents
auto-advance unless the existing reviewer-unavailable recovery classifies and
handles the failure.
Forbidden outcomes: a missing verdict increments accept count, Codex reports
`cursor_decision=accept`, or the gate reaches the next step without a recorded
degraded/escalation policy.
Related user stories: 3, 5, 6

P3. high-confidence-accept-advances

User-visible promise: The current single working reviewer with a normal
high-confidence accept still advances the gate.
Representative prompts or actions: Run `run_dual_agent_workflow` with the
existing Gemini/LiteLLM structured reviewer fixture returning decision
`accept`, severity `none`, and confidence above the configured threshold.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: final status and `codex_decision` match the current main
accept path, while the panel decision is recorded as accept.
Forbidden outcomes: clean accepts escalate by default, the low-confidence
threshold defaults to an aggressive value, or legacy reviewer payloads disappear.
Related user stories: 1, 5

P4. low-confidence-accept-escalates

User-visible promise: A reviewer accept below the configured confidence
threshold escalates instead of auto-advancing.
Representative prompts or actions: Configure a positive
`reviewer_low_confidence_threshold` and run `run_dual_agent_workflow` with an
accepting reviewer below that threshold.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: Codex records a non-accept decision with a panel reason of
low confidence; the panel decision lists low-confidence reviewers and preserves
the reviewer input.
Forbidden outcomes: low-confidence accept auto-advances, threshold is not
tunable, or confidence is ignored when present.
Related user stories: 4, 5

P5. reviewer-unavailable-recovery-preserved

User-visible promise: Existing reviewer infrastructure unavailable recovery
continues to classify and recover separately from panel quality aggregation.
Representative prompts or actions: Run the existing reviewer-unavailable
proceed-degraded and escalation tests with panel aggregation enabled.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: recoverable infrastructure failures still write
reviewer-unavailable recovery receipts, never count the missing reviewer as
accept, and advance only under the existing policy and available Claude/Codex
accepts.
Forbidden outcomes: panel aggregation masks the recovery event, treats
infrastructure failure as a quality revise, or weakens high-stakes escalation.
Related user stories: 3, 5, 6

## Implementation Decisions

- Add a small deterministic reviewer-panel evaluator rather than calibrated or
  dependence-weighted aggregation.
- Keep the panel evaluator independent from Cursor-specific result parsing so
  future reviewers can feed the same result shape.
- Add a configuration knob for low-confidence accept escalation with a
  permissive default.
- Wire the evaluator after `independent_reviewer_results[]` is created and
  before Codex computes the gate decision.
- Preserve `cursor_review`, `independent_reviewer`, `cursor_decision`, and
  legacy event compatibility while adding panel decision metadata.
- Preserve reviewer-unavailable recovery as a separate policy branch.
- Do not add a second real reviewer, weighting, or new gate sequence.

## Testing Decisions

- The first RED tests exercise `run_dual_agent_workflow`, the public boundary
  that actually advances or blocks gates.
- Tests fake external reviewer calls below the workflow boundary but keep the
  workflow route, reviewer result mapping, panel evaluator, and Codex decision
  composition intact.
- Public-boundary tests cover serious revise/deny blocking, missing verdict not
  accepting, high-confidence accept throughput, low-confidence escalation, and
  reviewer-unavailable recovery preservation.
- Helper tests may cover the evaluator only after the public-boundary behavior
  exists.
- Full suite validation remains required because the decision algebra is a
  shared gate surface.

## Out of Scope

- Calibrated weighting, lineage correlation, dependence scoring, or
  Ising-style aggregation.
- Adding a second real reviewer or changing the reviewer registry roster.
- Removing Cursor-named legacy compatibility.
- Changing P1/P2/P3/P13/P14 probes, gate order, or Claude lead defaults.
- Fixing external Cursor SDK infrastructure failures.

## Further Notes

This slice intentionally tightens trust in accept without loosening any block.
The only new non-accept case is a configured low-confidence accept escalation;
the default threshold is permissive so existing clean accept throughput is
preserved until eval data justifies a stricter default.
