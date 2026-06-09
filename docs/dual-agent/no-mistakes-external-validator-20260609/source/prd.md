# PRD: no-mistakes External Validator

Task id: `no-mistakes-external-validator-20260609`

## Problem Statement

Supervisor already has authoritative PRD, issue, TDD, implementation,
execution, outcome-review, Cursor, and reviewer-panel gates. It does not yet
have a post-acceptance branch validator that can run an external
review/test/document/lint loop before an operator ships the accepted diff. We
want to integrate `kunchenguid/no-mistakes` for that role without copying its
Go code or letting its score become supervisor truth.

The risky part is post-acceptance mutation. no-mistakes can run auto-fix,
commit, push, PR, and CI steps. If supervisor runs that loop in the active
worktree after `outcome_review` and still claims the old outcome is accepted,
the ledger lies about the final state. The integration must therefore be
optional, external-process based, isolated, ledger-backed, and unable to
advance or rewrite existing gate decisions.

## Solution

Add a supervisor-owned adapter in `supervisor/no_mistakes.py` that invokes the
external `no-mistakes` binary through subprocess with dependency injection for
tests. The default policy is `off`. Advisory and required modes build the safe
local command `no-mistakes axi run --intent ... --skip=push,pr,ci` and never
pass `--yes` unless an explicit config enables it.

For clean committed branches, the adapter creates a temporary detached Git
worktree and runs no-mistakes there. It snapshots changed files and HEAD before
and after execution, parses outcome and finding output, and returns a typed
result plus a `no_mistakes_validation_receipt`. The workflow invokes this only
after accepted `outcome_review` and reviewer review. Advisory findings are
evidence only; required or shipping findings block final completion through a
new post-acceptance validation result without rewriting the accepted
`outcome_review` gate.

## User Stories

1. As a supervisor operator, I can opt into external no-mistakes validation
   after supervisor acceptance without changing normal gate semantics.
2. As a reviewer, I can inspect no-mistakes findings as replayable ledger
   evidence with command, policy, cwd, skip steps, status, and receipt data.
3. As a maintainer, I can leave no-mistakes uninstalled on local machines and
   still run default supervisor workflows because the policy is off by default.
4. As a gate owner, I can trust that post-acceptance mutation marks the prior
   accepted outcome stale instead of silently shipping an unreviewed diff.

## PRD Promise Contracts

P1. External no-mistakes adapter
User-visible promise: Supervisor can run no-mistakes as an external subprocess
without importing or vendoring its implementation.
Representative prompts or actions: Build a validation request with a fake
runner and inspect the generated command and typed result.
Public boundary: `supervisor.no_mistakes.run_no_mistakes_validation`.
Allowed outcomes: command, stdout, stderr, exit code, duration, findings,
status, verdict, and receipt fields are captured.
Forbidden outcomes: Go source copied into supervisor, auto-install of the
binary, unbounded subprocess execution, or hidden workflow status mutation.
Related user stories: 1, 2

P2. Safe local policy defaults
User-visible promise: Default configuration is safe and does not ship.
Representative prompts or actions: Load `Config` and request advisory mode.
Public boundary: `supervisor.config.Config` and MCP workflow parameters.
Allowed outcomes: `policy=off`, default skip steps `push`, `pr`, and `ci`,
`auto_yes=false`, `allow_shipping_steps=false`, and optional per-call overrides.
Forbidden outcomes: push, PR, CI, `--yes`, commit, or auto-push by default.
Related user stories: 1, 3

P3. Ledger-backed post-acceptance evidence
User-visible promise: Workflow records no-mistakes evidence only after accepted
`outcome_review`, and no-mistakes cannot advance or replace supervisor gates.
Representative prompts or actions: Run an accepted workflow with an injected
no-mistakes runner.
Public boundary: `run_dual_agent_workflow`, `submit_dual_agent_workflow_job`,
and the event ledger.
Allowed outcomes: started, skipped, failed, completed, and finding events are
written with a `no_mistakes_validation_receipt`; prior accepted gate decisions
remain preserved.
Forbidden outcomes: no-mistakes bypasses P1/P2/P3/P11/P12/P13/P14, overwrites
`outcome_review`, or finalizes a required/shipping failure as accepted.
Related user stories: 2, 4

P4. Isolated stale-outcome guardrail
User-visible promise: A no-mistakes auto-fix or commit cannot silently change
the accepted active worktree.
Representative prompts or actions: Use a fake runner that writes files during
validation.
Public boundary: `run_no_mistakes_validation` and workflow finalization.
Allowed outcomes: validation runs in a temporary detached worktree for clean
branches, changed files produce `changed_requires_rerun`, and required/shipping
mode blocks finalization.
Forbidden outcomes: active worktree mutation from validation, accepted closeout
that ignores changed files, or treating no-mistakes output as gate authority.
Related user stories: 2, 4

## Implementation Decisions

- Use subprocess execution and dependency injection; do not import or vendor
  no-mistakes code.
- Keep `no_mistakes.policy` defaulted to `off` in config and example config.
- Enforce local advisory safety by keeping `push`, `pr`, and `ci` skipped
  unless shipping mode and explicit config allow them.
- Run the adapter after accepted `outcome_review` and reviewer-panel handling,
  then write ledger events and receipts as evidence.
- Preserve the supervisor gate truth model: no-mistakes findings may block a
  new post-acceptance validation result in required/shipping mode, but they do
  not rewrite earlier typed gate decisions.

## Testing Decisions

Tests must use fake subprocess runners, not a live no-mistakes install. Public
boundary coverage starts with `run_no_mistakes_validation`, then covers config,
detached workflow payload preservation, and `run_dual_agent_workflow` ordering.
The first workflow proof must show no-mistakes events occur after
`outcome_review`; the required-mode proof must show blocking without rewriting
the accepted outcome-review gate.

## Out of Scope

Installing no-mistakes, vendoring its repository, enabling push/PR/CI by
default, replacing Cursor review or the reviewer panel, auto-committing from
supervisor, or making no-mistakes a pre-outcome gate are outside this slice.
