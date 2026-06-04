# PRD Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Does "pure submit" leave new jobs idle forever without a dispatcher?
resolution: This Layer 0 slice uses `poll_dual_agent_workflow_job` as the
outside-submit recovery driver. Submit returns the durable job id and poll tool;
Layer 0.5 can add a lease dispatcher later without changing the reservation
contract.

### Finding G2

status: resolved
severity: high
question: Does the existing migration already provide enough idempotency?
resolution: Existing databases may have an all-token unique index from an
earlier migration, but the current table schema and future active-job invariant
are not explicit. This slice adds recovery columns and a forward migration that
replaces legacy uniqueness with the requested active-token uniqueness.

### Finding G3

status: resolved
severity: medium
question: Can terminal replay coexist with a partial active-job index?
resolution: The reservation API checks for any existing token first and replays
terminal rows, while the partial unique index is the database race backstop for
non-terminal active rows.

### Finding G4

status: resolved
severity: high
question: How can poll spawn later if submit no longer writes the request file?
resolution: The canonical request payload is persisted on the job row during
reservation. Poll writes the request file from that payload before spawning the
detached CLI worker.

### Finding G5

status: resolved
severity: medium
question: What happens to older rows that lack recovery metadata?
resolution: The migration infers `terminal`, `spawned`, or `reserved` from
terminal outcome and pid fields. Legacy terminal result polling remains intact,
and unspawnable legacy reservations fail durably instead of staying silent.

## Decision

All PRD grill findings are resolved. No waived or open findings remain.
