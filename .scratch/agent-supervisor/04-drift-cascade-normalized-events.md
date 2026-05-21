# Issue 04: Rebuild Drift Cascade on Normalized Events and Scope Contracts

## What to build

Move drift detection from Codex-shaped rollout payloads to normalized events and
immutable scope contracts. L1 should evaluate allowed, related, protected, and
never-touch paths. L2 should compare task text with intent summaries rather than
raw tool logs. L3 should return `on_plan`, `adjacent`, `blocked`,
`exploratory`, or `abandoned`. L4 should produce event-ID-cited verdicts.

## PRD promise

**Promise IDs:** P4, P5, P7

**User-visible promise:** Drift detection works the same way for Claude Code now
and Codex later, honors modes, and can be replayed from stored inputs.

**Public boundary:** `event_ingestion_api`, `mode_policy`, `replay_cli`

**Allowed outcomes:** protected or never-touch writes are severe; out-of-scope
writes gate later layers; replay can reproduce verdicts; event IDs support human
review.

**Forbidden outcomes:** raw Claude Code or Codex payloads are read in drift
logic; shadow mode injects steering; replay depends on current config.

**Representative prompt/action:** Replay a fixture containing a normalized
assistant plan, a protected-path write, and intent summaries.

## Acceptance criteria

- [ ] Drift detector consumes normalized events and scope snapshots only.
- [ ] L1 distinguishes out-of-scope, protected, and never-touch hits.
- [ ] Intent summaries are generated or accepted as derived events for L2.
- [ ] L3 uses the refined plan-status enum.
- [ ] L4 verdict schema includes classification, confidence,
      evidence_event_ids, recommended_action, and user_visible_summary.
- [ ] Mode policy controls whether the verdict becomes a would-do log,
      Telegram recommendation, or action.

## TDD plan

First public behavior: replaying a fixture with a never-touch write produces a
severe drift finding without reading raw target payloads.

RED: Add a `replay_cli` or replay-runner test using normalized event fixtures
and a frozen scope contract. Assert L1 flags the never-touch hit and the output
contains the source event ID.

GREEN: Refactor drift input to normalized events and scope contracts.

Next cycles:

- RED/GREEN for benign reads outside scope not counting as drift.
- RED/GREEN for out-of-scope writes gating L2.
- RED/GREEN for L3 refined statuses.
- RED/GREEN for shadow mode producing would-do action only.

## Blocked by

- `.scratch/agent-supervisor/01-target-adapter-foundation.md`
- `.scratch/agent-supervisor/02-state-snapshots-redaction.md`
