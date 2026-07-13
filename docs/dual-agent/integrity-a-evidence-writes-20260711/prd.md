# PRD: INTEGRITY-A Evidence Writes

## Problem Statement

Three evidence-write paths can corrupt or escape the supervisor record. A detached workflow can overwrite an already-recorded terminal outcome, a later P11 audit can erase an earlier audit revision, and an approved policy overlay or rollback backup can be written through a symlink outside the repository. These failures undermine terminal truth, historical auditability, and the repository write boundary.

## Solution

Make terminal completion a compare-and-set operation, append every quality-trend audit as an immutable revision while retaining a latest-value projection, and route policy-overlay plus rollback-backup writes through a repository-local no-follow writer. Conflicting terminal results must remain visible as discrepancy evidence, old audits must remain queryable, and every symlink escape must fail before mutation.

## User Stories

1. As an operator, I want the first terminal workflow outcome to remain authoritative, so that a stale worker cannot rewrite completed evidence.
2. As an investigator, I want a conflicting terminal result preserved beside the original, so that disagreement is observable instead of silently discarded.
3. As an auditor, I want every P11 quality audit revision retained, so that trend history can be reconstructed and compared over time.
4. As a trend consumer, I want the existing quality-trend row to expose the latest audit, so that current queries remain compatible.
5. As a repository owner, I want overlay and rollback writes confined to the real repository root, so that symlinks cannot redirect approved writes.
6. As a maintainer, I want normal in-repository policy approval and rollback behavior to remain unchanged, so that the hardening does not disable legitimate evolution.

## PRD Promise Contracts

P1. Terminal completion is immutable after the first successful compare-and-set.

- User-visible promise: an identical retry is a no-op; a different retry cannot alter the original terminal job row.
- Representative action: call `complete_dual_agent_workflow_job` twice for one job.
- Public boundary and seam: the `State.complete_dual_agent_workflow_job` interface.
- Allowed outcomes: one terminal-outcome event for identical retries; one discrepancy event containing both canonical outcomes for conflicts; a raised error on conflict.
- Forbidden outcomes: a second terminal event for an identical retry, mutation of original status/outcome fields, or an unrecorded conflict.

P2. Quality-trend audits are append-only revisions with a latest-value projection.

- User-visible promise: two audits for one run and gate remain separately queryable.
- Representative action: call `update_quality_trend_audit` twice and then query audit revisions plus the trend projection.
- Public boundary and seam: the `State` quality-trend interface and forward schema migration.
- Allowed outcomes: two rows keyed by run, gate, and monotonic computed time; the projection reflects the second revision; legacy audit fields are backfilled.
- Forbidden outcomes: in-place loss of the first audit, duplicate-key failure for same-second audits, or migration that strands historical projection data.

P3. Policy overlay and rollback writes cannot traverse symlinks.

- User-visible promise: target and backup paths must resolve inside the real repository root and contain no symlink component.
- Representative action: approve a proposal with a symlinked overlay file, overlay parent, or rollback directory.
- Public boundary and seam: `normalise_overlay_target`, policy approval, and rollback.
- Allowed outcomes: `PolicyOverlayError`-compatible failure before mutation; legitimate in-repository approval and rollback still succeed.
- Forbidden outcomes: bytes written outside the repository, following a final-file symlink, following a parent-directory symlink, or emitting an approval event after a rejected write.

## Implementation Decisions

- Compare canonical redacted terminal JSON, not caller dictionary ordering.
- Commit discrepancy evidence before raising the fail-closed conflict error.
- Store audit details and metrics in an immutable revision table with a composite key; retain the existing trend table as a rebuildable latest projection.
- Backfill legacy audit state during the forward migration.
- Centralize path containment, component checks, parent creation, and final-file opening behind one deep repository-write module.
- Use directory file descriptors and `O_NOFOLLOW` where the platform provides them.
- Preserve the existing operator-approval, hash verification, rollback pointer, and event interfaces.

## Testing Decisions

- Drive each behavior through the public state or policy-evolution interface.
- Verify both allowed and forbidden outcomes: no duplicate event, discrepancy event plus preserved original, two immutable audit rows, latest projection, migration backfill, rejected symlink writes, and successful legitimate writes.
- Keep fault injection below the policy-evolution interface so post-write hash verification remains covered after replacing `Path.write_bytes`.
- Run focused existing regression suites for workflow completion, quality audits, policy approval, rollback, and schema migrations.

## Out of Scope

- PostgreSQL state-method parity beyond the owned SQLite `State` implementation.
- Tamper-evident hash chaining or database triggers for all event and audit tables; those belong to LEDGER-001.
- Watcher normalization, runtime cancellation, replay checkout isolation, claim gates, or experiment-kernel work.
- Automatic policy approval or changes to reviewer/gate authority.

## Further Notes

The source plan named `supervisor/policy_evolution.py`; the live repository implementation is `supervisor/autoresearch/policy_evolution.py`. The latter is the actual approval and rollback write path hardened by this slice.
