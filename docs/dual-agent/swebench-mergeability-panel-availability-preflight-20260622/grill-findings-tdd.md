# TDD Grill Findings

### Finding 1

status: resolved

The tests needed to prove report behavior, not helper implementation details. Every planned test now drives the replay runner and inspects emitted preflight and S_full rows.

### Finding 2

status: resolved

The missing-reviewer and quality-reject cases could be conflated. Separate tests cover each case with the same configured-style panel shape and different reviewer roster evidence.

### Finding 2A

status: resolved

The TDD plan needed to cover omitted roster evidence separately from an explicit missing reviewer. A dedicated replay-runner test now drives configured mode with no reviewer_ids and expects missing_reviewer_roster.

### Finding 3

status: resolved

Reviewer evidence must stay public and auditable. The TDD plan checks ids, missing reviewers, transcript hashes, output hashes, and failure classifications without requiring hidden oracle fields.
