# Issues: Reviewer Panel Adjudication

## Slice 1: Build Deterministic Adjudication Packet

Priority: P0
Estimate: Medium

PRD promises: P1, P3, P4

Public boundary for first RED test: `run_dual_agent_workflow`

Representative action: Run the workflow with `cursor_review=true`, one
reviewer accepting, and one reviewer returning an important `revise` with cited
evidence refs.

Allowed outcomes: the workflow blocks through the existing conservative rule,
the panel decision includes adjudication metadata, and the packet names the
strongest objection plus refs, hashes, tests, and evidence-check status.

Forbidden outcomes: disagreement is resolved by majority vote, a real important
revise is softened, the strongest objection is hidden, or evidence inspection
reads outside the workflow cwd.

Scope: Detect split reviewer decisions, select the strongest objection
deterministically, assemble the bounded adjudication packet, and attach it to
the panel decision without changing conservative hard-block semantics.

Acceptance criteria:

- [ ] A split accept/revise panel triggers adjudication.
- [ ] The adjudication packet includes reviewer id, decision, severity,
      objection text, evidence refs, tests, transcript hash, and output hash.
- [ ] Local evidence refs are checked only under the workflow cwd.
- [ ] A real important/critical revise or deny still blocks.

TDD plan: add a `run_dual_agent_workflow` test with injected reviewers that
fails because no adjudication packet is present, then add the smallest helper
and workflow wiring to pass.

## Slice 2: Escalate Strong Accept-Shaped Objections

Priority: P0
Estimate: Medium

PRD promises: P2, P4

Public boundary for first RED test: `run_dual_agent_workflow`

Representative action: Run the workflow with both reviewers returning `accept`,
while one reviewer carries an important `critical_review.strongest_objection`.

Allowed outcomes: the adjudicator triggers `strong_minority_objection`, the
panel decision becomes `escalate`, Codex records a non-accept reviewer decision,
and the gate does not auto-advance.

Forbidden outcomes: all accepts auto-advance despite an important objection,
the objection is ignored because the top-level decision is accept, or the
system claims calibrated weighting.

Scope: Treat important/critical strongest objections inside accepting reviewer
results as adjudication triggers, and route them to escalation without
introducing vote counting or weighting.

Acceptance criteria:

- [ ] All-accept panel with an important strongest objection escalates.
- [ ] The round objection names adjudicated strong objection and reviewer id.
- [ ] Clean high-confidence accepts without strong objections still advance.

TDD plan: add a workflow-boundary test for accept-shaped objection escalation,
then wire adjudication decision composition so only strong objections change the
accept path.

## Slice 3: Persist And Export Adjudication Evidence

Priority: P1
Estimate: Small

PRD promises: P1, P4

Public boundary for first RED test: `read_gate_transcript` and dual-agent
artifact export

Representative action: After a split-panel workflow run, read the gate
transcript and exported `interactions.md` / `transcript.md`.

Allowed outcomes: the ledger includes an `independent_reviewer_adjudication`
event, `read_gate_transcript` returns adjudication records, and exported
artifacts render the trigger, decision, strongest objection, majority-vote
flag, evidence refs, tests, and evidence checks.

Forbidden outcomes: adjudication exists only in memory, legacy replay omits the
packet, transcript readers cannot explain the gate, or artifacts hide hashes.

Scope: Add the replay event, transcript projection, state event allowlist, and
artifact markdown rendering needed for durable adjudication receipts.

Acceptance criteria:

- [ ] `independent_reviewer_adjudication` events are written for triggered
      adjudications.
- [ ] `read_gate_transcript` exposes adjudication records.
- [ ] Exported interactions/transcript markdown render adjudication details.
- [ ] Legacy `independent_reviewer_review` and `tri_agent_cursor_review` events
      continue to carry panel decision metadata.

TDD plan: add a transcript/export assertion after the workflow-boundary
adjudication test, then add event recording and markdown rendering.

## Slice 4: Validate Regressions And Receipts

Priority: P1
Estimate: Small

PRD promises: P3, P4

Public boundary for first RED test: reviewer-panel workflow regression tests

Representative action: Re-run conservative panel, reviewer-unavailable,
artifact export, and full-suite tests.

Allowed outcomes: legacy cursor rejection text remains stable, reviewer
unavailable recovery still proceeds only through degraded policy, artifact
exports remain readable, and the full suite passes.

Forbidden outcomes: missing verdicts count as accept, degraded recovery
overrides a real reviewer objection, low-confidence threshold behavior changes,
or the full suite is skipped.

Scope: Preserve existing conservative aggregator and recovery behavior while
adding adjudication coverage and receipts.

Acceptance criteria:

- [ ] Focused adjudication and conservative panel tests pass.
- [ ] Legacy cursor rejection regression passes.
- [ ] Artifact/mailbox tests pass.
- [ ] Full repository suite passes.

TDD plan: run the focused reviewer-panel test pack, the legacy rejection
regression, artifact/mailbox tests, and the full suite; record receipts in the
workflow request.

## Coverage Index

| Promise | Issue claimant | Status |
| --- | --- | --- |
| P1. split-panel-adjudication-packet | Slice 1, Slice 3 | covered |
| P2. strong-minority-objection-escalates | Slice 2 | covered |
| P3. real-revise-deny-still-hard-blocks | Slice 1, Slice 4 | covered |
| P4. adjudication-evidence-is-replayable | Slice 1, Slice 3, Slice 4 | covered |
