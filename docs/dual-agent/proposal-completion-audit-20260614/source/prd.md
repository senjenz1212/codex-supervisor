# PRD: Proposal Completion Audit

## Problem Statement

The supervisor auto-improvement program now contains several connected mechanisms: report-only AutoResearch drafts, runnable experiment queues, policy overlay proposals, lessons, trend metrics, rollback drafting, runtime evidence floors, and the AXI detached-dispatcher path. The user asked whether the proposals are complete and whether the prior audit itself passed the supervisor rigorous gate. A credible answer needs a live, code-grounded supervisor run that separates implemented mechanisms from liveness evidence and from remaining operator decisions. The audit must not activate experiments, approve proposals, apply rollbacks, mutate policy overlays, or conflate a manual CLI with automatic behavior.

## Solution

Run a report-only supervised audit that reads production code, schema migrations, daemon wiring, AXI commands, ledger state, and generated artifacts. The lead must produce a durable outcome artifact that classifies each proposal or program area as implemented, partially implemented, missing, or live-unproven. The run must preserve the supervisor truth model: ledger events, typed gates, runtime receipts, and reviewer verdicts remain authoritative. The audit may use read-only CLI commands such as `codex-supervisor-axi doctor`, `codex-supervisor-axi experiments list`, `codex-supervisor-axi trends`, `git log`, `git show`, `rg`, and focused source reads. The audit must keep all policy-changing surfaces untouched.

## User Stories

1. As the operator, I want to know which auto-improvement proposals are complete so I do not activate or approve stale work by accident.
2. As the operator, I want the audit to distinguish automatic daemon behavior from manual CLI-only behavior because the auto-evolution loop is not real until triggers and cadences fire without hand steering.
3. As the operator, I want proposal status backed by code references, tests, migrations, and current ledger rows so the report is not just a confident narrative.
4. As the operator, I want the audit to remain report-only so it cannot mutate `.supervisor/policy-overlay.yaml`, approve proposals, or advance gates outside the supervisor flow.

## PRD Promise Contracts

P1. Code-Grounded Proposal Classification

- User-visible promise: The report lists every relevant auto-improvement area and marks it as implemented, partial, missing, or live-unproven with concrete evidence.
- Representative action: Run the supervised workflow for `proposal-completion-audit-20260614` and inspect the exported outcome report.
- Public boundary: `run_dual_agent_workflow` through the detached workflow worker, with evidence gathered through read-only source inspection and AXI queries.
- Allowed outcomes: accepted report-only audit; blocked gate with exact missing evidence; revised report that tightens references.
- Forbidden outcomes: claiming completion without code references; counting fixture-only behavior as live automation; hiding manual-only trigger gaps; applying policy as part of the audit.
- Related user stories: 1, 2, 3.

P2. Report-Only Safety Invariant

- User-visible promise: The audit cannot activate AutoResearch experiments, approve or apply policy proposals, write rollback changes, or mutate the policy overlay.
- Representative action: Compare pre-run and post-run AXI counts and policy overlay hashes.
- Public boundary: `codex-supervisor-axi experiments list`, policy evolution ledger reads, and filesystem hash checks for `.supervisor/policy-overlay.yaml`.
- Allowed outcomes: read-only report artifact; unchanged overlay hash; unchanged approval/apply state; explicit note if no open proposals exist.
- Forbidden outcomes: changing experiment status from draft to runnable; approving or denying proposals without operator instruction; mutating overlay YAML; advancing gates based on validator or trend metrics alone.
- Related user stories: 1, 4.

P3. Liveness And Denominator Check

- User-visible promise: The audit identifies where the loop is merely implemented versus live-proven, including the incident-rate denominator issue and any current draft/runnable/proposal counts.
- Representative action: Run `doctor`, `experiments list`, and `trends`, then inspect daemon/runner/audit wiring in code.
- Public boundary: `codex-supervisor-axi doctor`, `codex-supervisor-axi trends`, `codex-supervisor-axi experiments list`, and daemon scheduling code.
- Allowed outcomes: clear statement that there are zero, some, or many open drafts/runnable/proposals; clear statement of whether rates are normalized per run or merely shares of incidents; recommended next operator action.
- Forbidden outcomes: treating incident share as incident probability; declaring CLI migration decided without denominator data; treating a missing trend row as acceptance.
- Related user stories: 2, 3.

P4. Supervisor-Gate Compliance Evidence

- User-visible promise: The audit itself reports whether it passed the rigorous gates and which reviewers accepted, revised, or blocked.
- Representative action: Poll the workflow to terminal and inspect gate exports.
- Public boundary: `codex-supervisor-axi poll`, `codex-supervisor-axi catch-up`, exported `transcript.md`, `outcome-review.md`, and reviewer decisions.
- Allowed outcomes: terminal accepted with Cursor/Claude evidence where required; terminal blocked with exact blocker and next repair; no ambiguous "probably passed" claim.
- Forbidden outcomes: reporting gate success from a local-only audit; skipping P1/P2/P3/P11/P12/P13/P14; ignoring Cursor reviewer objections.
- Related user stories: 3, 4.

## Implementation Decisions

- Execution layer: use the durable detached workflow path. If the job is reserved, run or rely on the dispatcher; do not execute phases from poll.
- Lead policy: `lead_direct` with `agentic_lead_policy=off` for the audit itself; subagents are not required because the scope is read-only verification.
- Reviewer policy: Cursor SDK rigorous review remains enabled for TDD, implementation_plan, and outcome_review; reviewer unavailable policy is block.
- Visual evidence: not required because the audit is backend/report-only and makes no UI claim.
- Artifact location: source planning artifacts live under `docs/dual-agent/proposal-completion-audit-20260614/source/`; generated gate transcripts may overwrite root gate files.
- Mutation boundary: no source code changes are part of this audit. The only intended new artifact is a report under the audit directory, plus supervisor ledger/export files produced by the workflow itself.

## Testing Decisions

- Test command evidence is command-based rather than a new pytest suite because the deliverable is a report-only audit.
- The audit must run read-only commands that verify current state: `git status --short`, `codex-supervisor-axi doctor`, `codex-supervisor-axi experiments list --limit 100`, and `codex-supervisor-axi trends`.
- The lead must record whether each command succeeded and must include enough output summary to replay the conclusion.
- The workflow outcome review must confirm the report artifact exists, is non-empty, and contains the required classifications and safety invariants.

## Out Of Scope

- Activating or parking the 20 AutoResearch drafts.
- Applying, approving, or denying policy proposals.
- Changing daemon cadences, overlay semantics, lessons hygiene, trend formulas, runtime evidence floor, or Postgres DSN parity.
- Adding source-code tests or modifying production code in this audit slice.
- Deciding D1 or D2 if the available trend rows do not contain the correct denominator.
