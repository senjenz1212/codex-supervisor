# Issues: Reviewer Panel Second Reviewer

## Slice 1 - Add Codex CLI Reviewer Adapter

Priority: P1
Estimate: Small

PRD promises: P1, P2, P5

Public boundary for first RED test: `supervisor.reviewer_registry.configured_reviewers`

Representative action: Configure the reviewer panel with the default second
reviewer and inspect the returned specs.

Allowed outcomes: the registry returns the existing reviewer and a Codex CLI
reviewer with distinct `reviewer_id`, provider family `openai`, lineage,
`tool_access=codebase_tools`, and truthful assurance defaults.

Forbidden outcomes: production registry returns only one reviewer, uses a mock
second reviewer, or claims agentic assurance without transcript/hash support.

Scope: Add the Codex CLI reviewer adapter and register it as reviewer 1 behind
the existing reviewer registry, without changing aggregation semantics.

Acceptance criteria:

- [ ] `configured_reviewers` returns reviewer 0 plus a Codex CLI reviewer 1.
- [ ] The Codex CLI reviewer parses JSONL into a typed reviewer result.
- [ ] The reviewer result exposes runtime, lineage, tool access, assurance,
      transcript hash, and output hash metadata.

TDD plan: write a registry test that injects a fake Codex runner, proves the
Codex reviewer parses a typed outcome from JSONL, and checks transcript/output
hashes.

## Slice 2 - Run All Registry Reviewers In Workflow

Priority: P1
Estimate: Medium

PRD promises: P1, P3, P5

Public boundary for first RED test: `run_dual_agent_workflow`

Representative action: Run a workflow fixture with `cursor_review=true` and
two accepting reviewer fixtures.

Allowed outcomes: `independent_reviewer_results[]` contains both reviewers,
`independent_reviewer_review` carries both, legacy `cursor_review` remains
compatible with reviewer 0, and the conservative panel decision accepts both
available accepts.

Forbidden outcomes: only reviewer 0 is invoked, reviewer 1 appears in metadata
but is ignored by aggregation, or tests call live Codex by default.

Scope: Update `run_dual_agent_workflow` to invoke the full reviewer roster,
record all reviewer results, preserve reviewer-0 legacy payloads, and evaluate
the existing conservative panel over the full result list.

Acceptance criteria:

- [ ] A workflow with two accepting reviewers records both reviewer results.
- [ ] `independent_reviewer_review` carries the full panel.
- [ ] Legacy `cursor_review` and `tri_agent_cursor_review` remain readable.
- [ ] The conservative panel advances only when both available reviewers accept.

TDD plan: update workflow tests to inject fake reviewer runners and assert
two-result panel aggregation.

## Slice 3 - Preserve Honest Degraded Recovery

Priority: P1
Estimate: Medium

PRD promises: P3, P4, P5

Public boundary for first RED test: `run_dual_agent_workflow`

Representative action: Run a workflow fixture where reviewer 0 accepts and
reviewer 1 returns recoverable infrastructure unavailable under
`reviewer_unavailable_policy=proceed_degraded`.

Allowed outcomes: the workflow proceeds only through degraded recovery, records
which reviewer was unavailable, keeps the missing reviewer visible in the panel
decision, and never counts that verdict as accept.

Forbidden outcomes: unavailable reviewer is counted as accept, recovery hides
the outage, or degraded recovery bypasses a real reviewer revise/deny.

Scope: Extend reviewer-unavailable recovery detection from the legacy first
reviewer to the panel result list, while keeping the existing safety rails and
conservative hard-block rules.

Acceptance criteria:

- [ ] A single recoverable reviewer outage records the unavailable reviewer id.
- [ ] Proceed-degraded recovery never counts the missing verdict as accept.
- [ ] A real revise/deny from any available reviewer still blocks.

TDD plan: add a focused regression for single-reviewer outage and keep existing
reviewer-unavailable tests green.

## Slice 4 - Export Evidence And Run Full Validation

Priority: P2
Estimate: Small

PRD promises: P2, P5

Public boundary for first RED test: dual-agent artifact export and replay
manifest

Representative action: Run the dual-agent workflow and inspect exported
`interactions.md`, `outcome-review.md`, and replay artifacts.

Allowed outcomes: artifacts include route evidence, both reviewer results,
transcript refs/hashes, full test evidence, and accepted outcome-review.

Forbidden outcomes: artifact export omits the second reviewer, replay loses
hashes, or the suite is not run.

Scope: Export readable evidence and replay artifacts for the completed
supervised workflow and document the test and route evidence used for review.

Acceptance criteria:

- [ ] Focused reviewer-panel tests pass.
- [ ] The workflow-driver suite passes.
- [ ] The full repository suite passes.
- [ ] Exported artifacts include route evidence and both reviewer results.

TDD plan: focused tests, workflow-driver suite, full suite, and artifact export.
