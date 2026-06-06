# PRD: Execution Gate Deliverable Evidence

## Problem Statement

The execution gate currently tells the lead worker to implement real changes, but the
supervisor can still accept an `execution` gate when the typed outcome reports no
deliverable files or only incidental workflow artifacts. Outcome-review claim
verification catches some false claims, but it is claim-triggered and can run too late.

## Intent

Make execution and outcome-review gates deterministically require deliverable evidence:
accepted gates must name real changed files and matching receipts. Code tasks require
source/test/config diff evidence. Explicit docs/report-only work may satisfy the gate
with documented artifact exports, but only when the task scope says so.

## Solution

Add a deterministic `P11` deliverable-evidence probe that runs inside the workflow gate
loop for accepted `execution` and `outcome_review` results. The probe parses the typed
outcome, filters out incidental supervisor-generated artifacts, classifies remaining
deliverable files, and requires a covering diff/implementation/artifact receipt before
Codex can accept the round. If the probe is red, the gate records
`deliverable_evidence_failed` and does not spend reviewer calls on a known-hard failure.

## User Stories

1. As an operator, I want an accepted execution gate to prove that real deliverable
   files changed, not merely that the lead summarized a review.
2. As a reviewer, I want missing or stale diff receipts to block at execution before a
   later outcome-review claim can mask the failure.
3. As an eval/report author, I want explicit report-only artifact tasks to pass when
   the changed report artifact has a matching export receipt.

## PRD Promise Contracts

P1. Execution Requires Changed Deliverables

- User-visible promise: an accepted `execution` gate with no changed files is blocked.
- Public boundary: `run_dual_agent_workflow`.
- Allowed outcomes: `P11` is red with `accepted_gate_without_changed_files`.
- Forbidden outcomes: workflow status `accepted` when execution returns an empty
  `changed_files` list.
- Related user stories: 1

P2. Execution Requires Matching Diff/Artifact Receipts

- User-visible promise: code/test deliverables require a receipt that covers every
  reported changed file.
- Public boundary: `run_dual_agent_workflow`.
- Allowed outcomes: `P11` is red with `accepted_gate_without_deliverable_receipt`.
- Forbidden outcomes: a stale, partial, or absent diff receipt advancing execution.
- Related user stories: 2

P3. Incidental Workflow Artifacts Do Not Count

- User-visible promise: generated dual-agent artifacts such as transcripts, handoff
  files, and planning sources cannot satisfy execution evidence by themselves.
- Public boundary: deliverable verification inside the workflow gate loop.
- Allowed outcomes: incidental-only paths are treated as missing deliverables.
- Forbidden outcomes: `docs/dual-agent/<task>/source/*`, `.handoff/*`, or transcript
  files satisfying the implementation requirement.
- Related user stories: 1, 2

P4. Explicit Report-Only Artifacts Are Allowed

- User-visible promise: a report-only or docs-only task can pass with a report/doc
  artifact and matching artifact-export receipt.
- Public boundary: `run_dual_agent_workflow`.
- Allowed outcomes: `P11` is green for explicit report-only artifacts with covering
  receipts.
- Forbidden outcomes: arbitrary docs-only edits passing a code implementation task.
- Related user stories: 3

## Implementation Decisions

- Reuse `P11` as the deterministic deliverable-evidence probe because it already
  represents evidence/claim verification in gate decisions.
- Run deliverable verification for accepted `execution` and `outcome_review` gates.
- Skip Cursor/reviewer invocation when deterministic deliverable evidence already
  fails, avoiding extra cost after a hard supervisor failure.
- Keep the existing prompt contract as guidance, but make the gate decision depend on
  deterministic probe results.

## Testing Decisions

- The first RED tests use `run_dual_agent_workflow` because the promised behavior is
  gate advancement or blocking at the supervisor workflow boundary.
- Include negative cases for empty `changed_files` and missing/stale diff receipts.
- Include a positive explicit report-only artifact case to preserve ADR/eval/report
  workflows.
- Keep existing outcome-review claim-verification coverage for test and push claims,
  while expecting missing implementation receipts to block earlier at execution.

## Out of Scope

- No EvidenceAttempt ledger or async validator queue changes.
- No reviewer-panel policy changes.
- No new runtime or dispatcher behavior.
