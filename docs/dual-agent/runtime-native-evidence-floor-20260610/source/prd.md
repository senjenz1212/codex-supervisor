# Runtime-Native Evidence Floor PRD

## Problem Statement

The dual-agent workflow currently accepts execution and outcome evidence that can be authored by the same agent making the claim. A capable lead can claim that tests passed, that files changed, or that implementation work exists without the supervisor independently checking the repository state. This leaves a verification gap at the highest-risk gates: execution and outcome review.

The supervisor must make runtime evidence a first-class floor. Agent receipts can remain useful context, but they cannot be the authority for claims that require observing the local runtime, repository, filesystem, or test runner.

## Solution

At the execution and outcome review gates, the supervisor records the baseline git head, captures actual changed files and git name-status from the worktree, checks declared deliverables on disk, reruns declared test commands in an isolated validation copy, and emits receipts marked `source=supervisor` and `evidence_grade=runtime_native`.

Gate verification then requires those supervisor-owned receipts for implementation, test-pass, and changed-file coverage claims. Fabricated agent receipts remain visible evidence but cannot satisfy the runtime-native floor. Cursor SDK and Claude Code review continue to run as reviewers over the evidence; neither reviewer replaces deterministic runtime receipts.

## User Stories

- As an operator, I want execution gates to reject fabricated git or test receipts so that a lead cannot advance a change by self-reporting success.
- As an operator, I want outcome review to rely on supervisor-generated receipts so that final acceptance reflects the actual worktree and test execution.
- As a reviewer, I want runtime evidence events in the ledger so that I can replay why a gate accepted or blocked without trusting a transcript alone.
- As an implementer, I want the validation worktree isolated from the active worktree so that test reruns do not mutate the implementation environment.

## PRD Promise Contracts

P1. Runtime baseline contract: when execution or outcome review starts, the supervisor records the current git head and emits a runtime baseline receipt for that gate.

P2. Changed-file coverage contract: when a gate outcome declares changed files, the supervisor captures actual git status/name-status and rejects declared files that are missing from observed worktree changes.

P3. Deliverable existence contract: every declared deliverable file must exist as a file and be non-empty where applicable before the gate can advance.

P4. Test rerun contract: declared test commands are rerun by the supervisor in an isolated validation copy, and a claimed test pass requires a passing supervisor-owned test receipt.

P5. Evidence authority contract: agent-supplied receipts remain ledger evidence, but runtime-sensitive claims require receipts with `source=supervisor` and `evidence_grade=runtime_native`.

P6. Reviewer continuity contract: Cursor SDK rigorous review and Claude Code gate review still consume the evidence after deterministic runtime receipts are available, and reviewer acceptance cannot override a red runtime floor.

## Implementation Decisions

- Add a small `supervisor/runtime_evidence.py` module for baseline capture, changed-file collection, deliverable checks, validation-copy creation, and test reruns.
- Wire runtime evidence collection inside `run_dual_agent_workflow` immediately after a Claude gate accepts execution or outcome review and before Codex/Cursor gate advancement decisions.
- Store durable `dual_agent_runtime_evidence` ledger events and expose them through `read_gate_transcript`.
- Extend claim and deliverable verification to require `source=supervisor` and `evidence_grade=runtime_native` for runtime-sensitive claims.
- Keep fan-out, concurrency, reviewer panel, and config defaults unchanged in this slice.

## Testing Decisions

- Public-boundary tests use `run_dual_agent_workflow` rather than private helper-only checks.
- Negative tests cover fabricated runtime receipts, agent-only test-pass claims, and missing declared deliverables.
- Positive tests create a tiny real git repository, mutate real files after baseline, and rerun a passing test command from the supervisor validation copy.
- Transcript tests assert the runtime evidence event is durable and visible through the ledger reader.
- Full regression must keep existing workflow, MCP, reviewer, and artifact tests green.

## Out Of Scope

- No change to fan-out policy, concurrency defaults, reviewer panel behavior, or Cursor default selection.
- No adoption of a new external verifier service.
- No attempt to make agent-supplied receipts invalid; they remain advisory evidence.
- No automatic commit, push, CI, or production deployment behavior is added.
