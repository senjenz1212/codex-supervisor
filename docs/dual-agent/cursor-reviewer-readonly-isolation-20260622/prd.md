## Problem Statement

Cursor reviewer evidence is currently useful but not safe enough to count as full-panel mergeability evidence when the reviewer can mutate the same source worktree that the supervisor is trying to validate. Prior transcripts show `cursor_modified_worktree` blocking reviewer acceptance, and the current guard detects drift only after the risky execution path has already shared the operator worktree. Operators need Cursor to inspect current artifacts while any writes remain contained and diagnosable.

## Solution

Run Cursor reviewer execution through an explicit isolated-worktree interface that copies public review material into a disposable reviewer worktree, invokes Cursor there, and continues checking the original source worktree before and after review. The Cursor SDK sandbox option is enabled when available as defense in depth, but source-worktree safety is proven by physical isolation plus status checks rather than by trusting Cursor mode or prompt wording.

## User Stories

1. As a supervisor operator, I want Cursor review writes contained away from the source worktree, so that reviewer evidence cannot erase or alter implementation evidence.
2. As a mergeability evaluator, I want contained Cursor writes recorded as diagnostics, so that I can distinguish safe containment from silent reviewer side effects.
3. As a benchmark runner, I want full-panel S_full evidence only when Cursor and Codex both return valid safe verdicts, so that missing reviewer evidence is never treated as acceptance.
4. As a reviewer-packet author, I want hidden oracle material excluded from Cursor prompts and worktrees, so that benchmark labels remain independent of reviewer decisions.
5. As a calibration user, I want Codex-only fallback preserved as calibration-only, so that partial reviewer evidence is not mislabeled as full-panel evidence.

## PRD Promise Contracts

P1. Cursor reviewer execution cannot mutate the original source worktree even if Cursor writes files while reviewing.
P2. Cursor reviewer mutation inside an isolated disposable worktree is recorded as a diagnostic with enough detail for audit.
P3. Full-panel S_full evidence is available only when Cursor and Codex reviewer verdicts are both valid, public-only, and free of unsafe source-worktree mutation.
P4. Codex-only fallback remains labeled calibration-only and cannot be reported as full-panel evidence.
P5. Hidden oracle material never enters Cursor reviewer prompts, reviewer packets, isolated worktrees, transcripts, or receipts.

## Implementation Decisions

The primary seam is `invoke_cursor_agent`, because configured reviewer panels already consume `CursorInvocationResult` and registry aggregation already treats missing verdicts conservatively. The implementation will add an explicit Cursor worktree isolation option to the request interface, use a disposable copied reviewer worktree for Cursor SDK execution, keep the original source `git status` guard as a backstop, and attach isolation diagnostics to reviewer metadata. The SDK sandbox option will be passed when available, but it will not be documented or tested as a read-only guarantee.

## Testing Decisions

Tests will begin at the public reviewer invocation boundary by driving `invoke_cursor_agent` with a fake Cursor runner that writes to its cwd. The first tests must prove the original git worktree stays unchanged, contained isolated writes are reported, and the older non-isolated path still blocks unsafe source mutations. Follow-up tests can inspect the SDK adapter seam to prove sandbox options are passed and the mergeability panel path to prove unsafe or missing Cursor verdicts keep S_full unavailable.

## Out of Scope

This slice does not prove Cursor reviewer quality, run a powered mergeability benchmark, change oracle semantics, or promote any policy proposal. It does not turn Codex-only calibration into production full-panel evidence. It does not claim Cursor’s own sandbox or plan mode is read-only; those mechanisms remain secondary containment behind the isolated reviewer worktree.
