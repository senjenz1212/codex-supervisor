# Issues: Reviewer Panel Conservative Aggregator

## Slice 1: Evaluate reviewer panel results conservatively

Type: AFK
Priority: P0
Blocked by: reviewer-panel-foundation-20260601

Scope: Add a deterministic panel evaluator for `independent_reviewer_results[]`
that returns `accept`, `revise`, or `escalate` plus structured reasons and input
summaries. It must hard-block critical or important revise/deny, treat missing
verdicts as non-accepting, accept only available real accepts, and classify
low-confidence accepts by a tunable threshold.

PRD promise: P1, P2, P3, P4

Acceptance criteria:

- [ ] Critical or important revise/deny returns a blocking panel decision with
      reviewer ids.
- [ ] Missing or malformed verdict returns a missing-verdict panel decision and
      never returns accept.
- [ ] High-confidence accept returns accept with current single-reviewer
      throughput preserved.
- [ ] Low-confidence accept below a configured threshold returns escalation.
- [ ] Evaluator records threshold, low-confidence reviewers, missing reviewers,
      blocking reviewers, and available reviewers.

First public-boundary RED test: `run_dual_agent_workflow` with cursor review
enabled and a serious reviewer revise/deny blocks the gate while recording the
panel decision.

## Slice 2: Wire conservative panel decision into workflow gates

Type: AFK
Priority: P0
Blocked by: Slice 1

Scope: Replace the legacy single `cursor_accepts(...)` decision source with the
panel evaluator output inside the workflow gate loop while preserving the
existing compatibility fields and reviewer-unavailable recovery branch.

PRD promise: P1, P2, P3, P4, P5

Acceptance criteria:

- [ ] `cursor_decision` remains present for compatibility but derives from the
      panel decision.
- [ ] `independent_reviewer_panel_decision` appears on `payload`,
      `cursor_review`, `independent_reviewer_review`, legacy
      `tri_agent_cursor_review`, gate result, and Codex review metadata where
      applicable.
- [ ] Reviewer-unavailable proceed-degraded and escalation behavior remains
      unchanged for infrastructure failures.
- [ ] Clean high-confidence accept reaches `outcome_review` as before.
- [ ] Low-confidence accept causes a non-accept round and does not advance.

First public-boundary RED test: `run_dual_agent_workflow` with a low-confidence
accept below threshold records panel escalation and does not auto-advance.

## Slice 3: Add config and replay-safe artifacts

Type: AFK
Priority: P1
Blocked by: Slice 2

Scope: Add a `reviewer_low_confidence_threshold` supervisor configuration knob
with a permissive default, document it in the example config, and ensure the
threshold and panel decision are replay-visible in event payloads and exported
artifacts.

PRD promise: P3, P4, P5

Acceptance criteria:

- [ ] Default threshold is permissive and does not escalate normal accepts.
- [ ] Config can raise the threshold for tests or high-stakes deployments.
- [ ] Panel decision metadata survives transcript reads and artifact exports.
- [ ] Deterministic replay retains per-reviewer inputs and panel decision
      reasons.

First public-boundary RED test: `run_dual_agent_workflow` with default config
accepts a high-confidence reviewer, and the same boundary with a stricter config
escalates a lower-confidence accept.

## Coverage Index

| Promise | Covered by | Status |
| --- | --- | --- |
| P1 important-reviewer-objection-blocks | Slice 1, Slice 2 | Covered |
| P2 missing-reviewer-verdict-never-accepts | Slice 1, Slice 2 | Covered |
| P3 high-confidence-accept-advances | Slice 1, Slice 2, Slice 3 | Covered |
| P4 low-confidence-accept-escalates | Slice 1, Slice 2, Slice 3 | Covered |
| P5 reviewer-unavailable-recovery-preserved | Slice 2, Slice 3 | Covered |
