# PRD: Reviewer Panel Adjudication

## Problem Statement

The reviewer panel now records multiple independent reviewer results and applies
conservative non-weighted aggregation. That protects the gate from obvious
serious revise/deny verdicts, but it still treats disagreement as a policy
state rather than a structured audit object. When reviewers split, or when an
accepting reviewer carries a strong objection in its critical review, the
workflow should preserve the exact objection, evidence refs, hashes, and tests
that explain why the objection matters.

Operators need disagreement to become an auditable adjudication packet, not a
majority vote or an opaque "panel did not accept" string. The packet must be
bounded, replayable, and tied to evidence that can be inspected without
weakening the existing rule that any real important or critical revise/deny
hard-blocks.

## Solution

Add a deterministic reviewer-panel adjudication layer after
`independent_reviewer_results[]` are built and before the workflow records the
gate decision. The adjudicator detects split reviewer decisions and strong
minority objections, chooses the strongest objection by severity and reviewer
signal, assembles an adjudication packet with evidence refs, transcript/output
hashes, tests, and reviewer provenance, and performs bounded evidence checks on
local refs when they are available.

The adjudicated outcome is recorded on the panel decision and reviewer events.
It may preserve an existing hard block, escalate a strong objection from an
otherwise accepting panel, or record that adjudication was not triggered. It
does not introduce calibrated weighting, majority vote, or any path that
softens a real critical or important revise/deny.

## User Stories

1. As a gate operator, I want a split panel to produce a structured
   adjudication packet, so that I can see the exact objection and evidence that
   drove the block or escalation.
2. As an auditor, I want adjudication packets to include cited refs, hashes,
   tests, reviewer ids, runtime lineage, and assurance grade, so that replay can
   reconstruct what was inspected.
3. As a workflow maintainer, I want a minority but important objection to block
   or escalate instead of being hidden behind overall accept language, so that
   safety-critical findings are not outvoted.
4. As a reviewer, I want all real critical or important revise/deny verdicts to
   remain hard-blocking, so that adjudication cannot weaken the conservative
   panel rules.
5. As a replay consumer, I want `independent_reviewer_review` and legacy
   `tri_agent_cursor_review` artifacts to expose the adjudication result, so
   that old and new readers can explain the gate.

## PRD Promise Contracts

P1. split-panel-adjudication-packet

User-visible promise: A panel split between accept and revise/deny records a
bounded adjudication packet over the strongest objection.
Representative prompts or actions: Run `run_dual_agent_workflow` with
`cursor_review=true`, one reviewer accepting, and another reviewer returning
`revise` with `critical_review.severity=important`.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the workflow blocks through the existing conservative rule,
the panel decision includes adjudication metadata, and the adjudication packet
names the strongest reviewer objection plus refs/hashes/tests inspected.
Forbidden outcomes: disagreement is resolved by majority vote, the objection is
hidden behind a generic panel reason, or the gate advances while a real
important revise is present.
Related user stories: 1, 2, 4, 5

P2. strong-minority-objection-escalates

User-visible promise: If reviewers all return accept but one carries a strong
important or critical objection, the workflow escalates instead of
auto-advancing.
Representative prompts or actions: Run `run_dual_agent_workflow` with both
reviewers accepting and one accept result whose `critical_review` includes a
non-`none` strongest objection at `important` severity.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the panel decision records adjudication with decision
`escalate`, `cursor_decision` becomes non-accept, and the strongest objection is
visible in gate artifacts.
Forbidden outcomes: the strong objection is ignored because the reviewer also
returned accept, all accepts auto-advance despite the important objection, or
the system claims weighting or majority vote.
Related user stories: 1, 3, 5

P3. real-revise-deny-still-hard-blocks

User-visible promise: A real critical or important revise/deny remains a hard
block even after adjudication is added.
Representative prompts or actions: Re-run the existing conservative panel
fixtures for important reviewer revise and second-reviewer important revise.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the same blocked status is preserved, with extra adjudication
metadata explaining the strongest objection.
Forbidden outcomes: adjudication downgrades a real revise/deny to accept,
requires a majority to block, or lets degraded reviewer-unavailable recovery
override a real objection.
Related user stories: 3, 4

P4. adjudication-evidence-is-replayable

User-visible promise: The adjudication packet records bounded evidence refs,
hash validation results, transcript/output hashes, and tests without requiring
live reviewers during replay.
Representative prompts or actions: Inspect `independent_reviewer_review`,
`tri_agent_cursor_review`, and exported `interactions.md`/`transcript.md` after
a split-panel run.
Public boundary: `independent_reviewer_review` event payload and exported
dual-agent artifacts
Allowed outcomes: the packet includes reviewer id, decision, severity,
strongest objection, evidence refs, transcript refs/hashes, output hash, tests,
bounded file check status, and a replayable adjudication decision.
Forbidden outcomes: adjudication depends on ambient credentials in tests,
stores raw secrets, reads outside bounded workspace refs, or omits hashes from
artifacts.
Related user stories: 2, 5

## Implementation Decisions

- Add a small deterministic adjudication helper next to reviewer panel helpers.
- Trigger adjudication on decision disagreement or strong objections at
  important/critical severity, including objections embedded in accepting
  reviewer critical reviews.
- Pick the strongest objection by severity, non-accept decision, confidence,
  and reviewer id tie-breakers.
- Build an adjudication packet from existing reviewer result fields:
  `critical_review`, `transcript_refs`, hashes, lineage, assurance grade, tests,
  and evidence refs.
- Bound evidence inspection to local, relative refs under the workflow cwd and
  cap the number of refs inspected.
- Attach adjudication to `independent_reviewer_panel_decision`,
  `cursor_review`, and reviewer events.
- Record an `independent_reviewer_adjudication` ledger event for replay.
- Preserve existing conservative aggregation: real important/critical
  revise/deny remains a hard block, missing verdicts do not count as accept,
  and low-confidence escalation remains unchanged.

## Testing Decisions

- The first RED tests exercise `run_dual_agent_workflow`, not the helper, so
  tests cover reviewer invocation, panel evaluation, adjudication attachment,
  Codex decision composition, events, and artifacts together.
- External reviewer calls remain injected below the workflow boundary; tests do
  not call live Cursor, Codex CLI, or LiteLLM.
- Public-boundary tests cover split-panel adjudication, strong accepting
  objection escalation, hard-block regression, and replay/event export.
- Helper-level tests may cover evidence packet ordering and bounded ref checks
  only after the workflow-boundary tests exist.
- Full-suite validation is required because this touches shared gate decision
  algebra and transcript/export projections.

## Out of Scope

- Calibrated weighting, dependence scoring, Ising-style aggregation, or
  majority vote.
- Adding a new real reviewer route.
- Changing P-probe order, the Claude lead, reviewer-unavailable recovery, or
  the low-confidence threshold default.
- Removing legacy `cursor_review` or `tri_agent_cursor_review` compatibility.
- Fixing Cursor SDK infrastructure failures.

## Further Notes

This slice makes disagreement more inspectable. It does not make the panel more
permissive: adjudication can only preserve a block or add escalation for a
strong objection that would otherwise be hidden by accept-shaped verdicts.
