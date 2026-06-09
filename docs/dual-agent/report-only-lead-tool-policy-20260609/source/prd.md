# PRD: Report-Only Lead Tool Policy

## Problem Statement

Report-only supervised workflows can legitimately need the execution lead to edit a
bounded report artifact and capture verification receipts. The current execution
gate prompt already says report-only tasks may edit requested report artifacts,
but the Claude Code command does not provide a narrow write-capable tool policy
for that case. The result is a blocked report-only run even when P11 would accept
a real changed report file with matching receipts.

## Solution

Add a narrowly scoped command-level allowed-tools policy only for explicit
report-only execution requests. The policy grants read, edit, write, git status,
git diff, and pytest receipt commands needed to author and verify the report
deliverable. Normal code execution requests keep the existing command shape, and
the report-only branch uses a non-bypass permission mode so the allowed-tools
list is the effective lead tool policy. P11 remains the authoritative
deliverable-evidence check.

## User Stories

- As an operator running a report-only Vela AutoResearch trial, I need the lead to
  edit the requested report artifact and cite receipt evidence so the workflow can
  complete through execution and outcome review.
- As a supervisor maintainer, I need normal implementation tasks to avoid a
  broader write policy unless the request is explicitly report-only.
- As a reviewer, I need P11 to keep blocking accepted outcomes that claim a
  report deliverable without listing a real changed file and matching receipt.

## PRD Promise Contracts

P1. Explicit report-only execution requests receive a non-bypass permission mode
and narrow write-capable tools limited to report authoring, diff inspection, and
focused pytest receipt capture.

P2. Normal execution requests do not receive the report-only allowed-tools list,
so ordinary code tasks preserve the existing lead invocation boundary.

P3. P11 remains strict: report-only outcomes pass only when the outcome lists the
actual docs/report deliverable as changed and a receipt covers the same file.

## Implementation Decisions

- Implement the policy in `supervisor/dual_agent_lead.py` at command construction
  time so the permission-mode and allowed-tools changes are local to Claude Code
  invocation.
- Detect report-only execution from the execution gate plus report-only markers
  and docs/report deliverable markers in the instruction, task id, or handoff
  path.
- Add tests around `build_claude_lead_command` for positive and negative command
  shapes.
- Add workflow-driver regressions around Vela-style report artifacts to prove P11
  allows covered report deliverables and blocks receipt-only claims without
  changed files.

## Testing Decisions

The first tests cover the public command boundary because the bug is a missing
Claude Code invocation permission. The second set covers the workflow-driver
P11 boundary so this slice cannot accidentally weaken deliverable evidence. The
focused test commands are enough to prove the policy branch and the guardrail
branch before running the larger targeted files.

## Out Of Scope

- No global permission-mode change.
- No broad `Bash(*)` access.
- No change to P11 semantics or outcome-review authority.
- No change to Vela product code or Vela report artifacts in this repository.
- No weakening of planning, execution, or reviewer gates.
